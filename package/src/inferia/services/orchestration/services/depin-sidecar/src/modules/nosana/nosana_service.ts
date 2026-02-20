import { createNosanaClient, NosanaClient, NosanaNetwork, getJobExposedServices, JobState } from '@nosana/kit';
import { address, createKeyPairSignerFromBytes } from '@solana/kit';
import bs58 from 'bs58';
import type { JobDefinition } from '@nosana/types';
import { LogStreamer } from './nosana_logs';

// Job timing constants (in milliseconds)
const JOB_TIMEOUT_MS = 30 * 60 * 1000;
const EXTEND_THRESHOLD_MS = 5 * 60 * 1000;
const EXTEND_DURATION_SECS = 1800;
const MIN_RUNTIME_FOR_REDEPLOY_MS = 20 * 60 * 1000;

// Nosana Dashboard API constants
const NOSANA_API_BASE_URL = process.env.NOSANA_API_URL || 'https://dashboard.k8s.prd.nos.ci/api';
const SIGN_MESSAGE = 'Hello Nosana Node!';

interface WatchedJobInfo {
    jobAddress: string;
    deploymentUuid?: string;         // Required for API-mode auth
    startTime: number;
    lastExtendTime: number;
    jobDefinition: any;
    marketAddress: string;
    isConfidential?: boolean;
    resources: {
        gpu_allocated: number;
        vcpu_allocated: number;
        ram_gb_allocated: number;
    };
    userStopped: boolean;
    serviceUrl?: string;
    credentialName?: string;        // Which credential was used for this job
}

async function retry<T>(fn: () => Promise<T>, retries = 5, delay = 500): Promise<T> {
    try {
        return await fn();
    } catch (error: any) {
        const errorMsg = error.message || "";
        if (retries > 0 && (errorMsg.includes("429") || errorMsg.includes("Too Many Requests"))) {
            console.log(`[retry] Got 429, retrying in ${delay}ms... (${retries} left)`);
            await new Promise(resolve => setTimeout(resolve, delay));
            // Backoff: 500ms -> 1s -> 2s -> 4s -> 8s
            return retry(fn, retries - 1, delay * 2);
        }
        throw error;
    }
}

export class NosanaService {
    private client: NosanaClient;
    private privateKey: string | undefined;
    private apiKey: string | undefined;
    private authMode: 'wallet' | 'api' = 'wallet';
    private watchedJobs = new Map<string, WatchedJobInfo>();
    private summaryInterval: number = 60000;
    // Cache for API-signed auth headers
    private cachedApiAuth: { signature: string; message: string; userAddress: string; timestamp: number } | null = null;
    private readonly API_AUTH_CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

    constructor(options: { privateKey?: string, apiKey?: string, rpcUrl?: string }) {
        this.privateKey = options.privateKey;
        this.apiKey = options.apiKey;

        if (this.apiKey) {
            this.authMode = 'api';
            this.client = createNosanaClient(NosanaNetwork.MAINNET, {
                api: { apiKey: this.apiKey },
                solana: {
                    rpcEndpoint: options.rpcUrl || "https://api.mainnet-beta.solana.com",
                },
            });
        } else {
            this.authMode = 'wallet';
            this.client = createNosanaClient(NosanaNetwork.MAINNET, {
                solana: {
                    rpcEndpoint: options.rpcUrl || "https://api.mainnet-beta.solana.com",
                },
            });
        }

        this.startWatchdogSummary();
    }

    /**
     * Get the authorization header for API mode
     */
    private getApiAuthHeader(): string {
        if (!this.apiKey) throw new Error('API key not configured');
        return `Bearer ${this.apiKey}`;
    }

    /**
     * Make an authenticated API request to Nosana Dashboard API
     */
    private async apiRequest<T>(path: string, options: {
        method?: string;
        body?: any;
        headers?: Record<string, string>;
    } = {}): Promise<T> {
        const { method = 'GET', body, headers = {} } = options;

        const url = `${NOSANA_API_BASE_URL}${path}`;
        const fetchHeaders: any = {
            'Authorization': this.getApiAuthHeader(),
            ...headers
        };

        const fetchOptions: RequestInit = {
            method,
            headers: fetchHeaders
        };

        // Only set Content-Type and body if we actually have a body to send
        // This is critical for endpoints like /jobs/{address}/stop that don't accept a body
        if (body !== undefined && body !== null && method !== 'GET') {
            fetchHeaders['Content-Type'] = 'application/json';
            fetchOptions.body = JSON.stringify(body);
        }

        const response = await fetch(url, fetchOptions);

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Nosana API Error (${response.status}): ${errorText}`);
        }

        return response.json() as Promise<T>;
    }

    /**
     * Sign a message using the Nosana API external signing endpoint
     * This is used for API mode to get signatures for node authentication
     * POST /api/auth/sign-message/external
     */
    async signMessageExternal(message: string): Promise<{ signature: string; message: string; userAddress: string }> {
        if (this.authMode !== 'api') {
            throw new Error('signMessageExternal is only available in API mode');
        }

        // Check cache
        const now = Date.now();
        if (
            this.cachedApiAuth &&
            this.cachedApiAuth.message === message &&
            (now - this.cachedApiAuth.timestamp) < this.API_AUTH_CACHE_TTL_MS
        ) {
            console.log('[API Auth] Using cached signature');
            return {
                signature: this.cachedApiAuth.signature,
                message: this.cachedApiAuth.message,
                userAddress: this.cachedApiAuth.userAddress
            };
        }

        console.log(`[API Auth] Requesting signed message from Nosana API...`);
        const result = await this.apiRequest<{ signature: string; message: string; userAddress: string }>(
            '/auth/sign-message/external',
            {
                method: 'POST',
                body: { message }
            }
        );

        // Cache the result
        this.cachedApiAuth = {
            ...result,
            timestamp: now
        };

        console.log(`[API Auth] Received signature for user: ${result.userAddress}`);
        return result;
    }

    /**
     * Generate authentication header for node communication in API mode
     * Format: MESSAGE:SIGNATURE (same as wallet mode)
     */
    async generateApiNodeAuthHeader(): Promise<{ header: string; userAddress: string }> {
        const auth = await this.signMessageExternal(SIGN_MESSAGE);
        return {
            header: `${auth.message}:${auth.signature}`,
            userAddress: auth.userAddress
        };
    }

    /**
     * Get job details from the Nosana Dashboard API
     * GET /api/jobs/{address}
     * This provides additional metadata about jobs that is not available from on-chain data.
     */
    async getJobFromApi(jobAddress: string): Promise<{
        owner: string;
        market: string;
        node: string;
        ipfsJob: string;
        ipfsResult: string | null;
        timeStart: number;
        timeEnd: number | null;
        status: number;
        state: string;
        marketName: string | null;
    } | null> {
        if (this.authMode !== 'api') {
            return null;
        }

        try {
            const jobDetails = await this.apiRequest<{
                owner: string;
                market: string;
                node: string;
                ipfsJob: string;
                ipfsResult: string | null;
                timeStart: number;
                timeEnd: number | null;
                status: number;
                state: string;
                marketName: string | null;
            }>(`/jobs/${jobAddress}`);

            return jobDetails;
        } catch (error: any) {
            console.warn(`[API] Failed to get job details for ${jobAddress}:`, error.message);
            return null;
        }
    }

    markJobAsStopping(jobAddress: string): void {
        const jobInfo = this.watchedJobs.get(jobAddress);
        if (jobInfo) {
            jobInfo.userStopped = true;
            console.log(`[user-stop] Marked job ${jobAddress} as user-stopped`);
        }
    }

    async init() {
        if (this.authMode === 'wallet' && this.privateKey) {
            try {
                const secretKey = bs58.decode(this.privateKey);
                const signer = await createKeyPairSignerFromBytes(secretKey);
                this.client.wallet = signer;
                const walletAddr = this.client.wallet ? this.client.wallet.address : "Unknown";
                console.log(`Nosana Adapter initialized in WALLET mode. Wallet: ${walletAddr}`);
            } catch (e) {
                console.error("Failed to initialize Nosana wallet:", e);
                throw e;
            }
        } else if (this.authMode === 'api') {
            console.log("Nosana Adapter initialized in API mode.");
        }
    }

    async launchJob(jobDefinition: any, marketAddress: string, isConfidential: boolean = true) {
        try {
            // Step A: Upload to IPFS
            let definitionToPin = jobDefinition;
            if (isConfidential) {
                console.log("[Launch] Confidential mode ACTIVE. Preparing dummy job definition...");
                definitionToPin = {
                    version: jobDefinition.version || "0.1",
                    type: jobDefinition.type || "container",
                    meta: {
                        ...jobDefinition.meta,
                        trigger: "cli"
                    },
                    logistics: {
                        send: { type: "api-listen", args: {} },
                        receive: { type: "api-listen", args: {} }
                    },
                    ops: []
                };

                if (jobDefinition.logistics) {
                    if (jobDefinition.logistics.send && jobDefinition.logistics.send.type === 'api') {
                        definitionToPin.logistics.send = jobDefinition.logistics.send;
                    }
                    if (jobDefinition.logistics.receive && jobDefinition.logistics.receive.type === 'api') {
                        definitionToPin.logistics.receive = jobDefinition.logistics.receive;
                    }
                }
            } else {
                console.log("[Launch] Confidential mode INACTIVE. Pinning full job definition.");
            }

            let jobAddress = "unknown";
            let deploymentUuid: string | undefined;
            let ipfsHash = "pending";

            if (this.authMode === 'api') {
                console.log(`[Launch] Posting job via API in market: ${marketAddress}`);

                // Step 1: Pin the job definition to IPFS first
                // The API requires an ipfsHash, not a raw job_definition
                console.log(`[Launch] Pinning job definition to IPFS...`);
                ipfsHash = await this.client.ipfs.pin(definitionToPin);
                console.log(`[Launch] IPFS Hash: ${ipfsHash}`);

                // Step 2: Use the direct HTTP API: POST /api/jobs/list
                // This endpoint expects: { ipfsHash: string, market: string }
                try {
                    console.log(`[Launch] Using direct HTTP API POST /api/jobs/list...`);
                    const jobListResult = await this.apiRequest<{
                        tx: string;
                        job: string;
                        credits: { costUSD: number; creditsUsed: number; reservationId: string };
                    }>('/jobs/list', {
                        method: 'POST',
                        body: {
                            ipfsHash: ipfsHash,
                            market: marketAddress
                        }
                    });

                    jobAddress = jobListResult.job;
                    console.log(`[Launch] Job posted via direct API. Address: ${jobAddress}, TX: ${jobListResult.tx}, Credits Used: ${jobListResult.credits.creditsUsed}`);
                } catch (apiError: any) {
                    // Fallback: Try SDK deployment API if direct API fails
                    console.warn(`[Launch] Direct API failed: ${apiError.message}. Falling back to SDK deployments API...`);

                    const deployment = await this.client.api.deployments.create({
                        name: `inferia-${Date.now()}`,
                        market: marketAddress,
                        job_definition: definitionToPin,
                        replicas: 1,
                        timeout: 3600,
                        strategy: 1, // Fix/Deterministic strategy
                    } as any);

                    deploymentUuid = (deployment as any).uuid || (deployment as any).id;
                    console.log(`[Launch] Deployment created: ${deploymentUuid}. Waiting for Job Address...`);

                    // Bridge: Poll for Job Address
                    let attempts = 0;
                    while (attempts < 30) {
                        const status = await this.client.api.deployments.get(deploymentUuid!);
                        const jobs = (status as any).jobs || [];
                        if (jobs.length > 0) {
                            jobAddress = jobs[0].address || jobs[0].job;
                            ipfsHash = jobs[0].ipfs_job || ipfsHash;
                            console.log(`[Launch] Resolved Job Address from SDK: ${jobAddress}`);
                            break;
                        }
                        await new Promise(r => setTimeout(r, 2000));
                        attempts++;
                    }

                    if (jobAddress === "unknown") {
                        throw new Error("Timeout waiting for Job Address from Nosana API");
                    }
                }

            } else {
                // Wallet Mode: Legacy Flow
                console.log("Pinning job to IPFS...");
                ipfsHash = await this.client.ipfs.pin(definitionToPin);
                console.log(`IPFS Hash: ${ipfsHash}`);

                console.log(`Listing on market: ${marketAddress}`);
                const instruction = await this.client.jobs.post({
                    ipfsHash,
                    market: address(marketAddress),
                    timeout: 1800,
                });

                if (instruction.accounts && instruction.accounts.length > 0) {
                    jobAddress = instruction.accounts[0].address;
                }

                const signature = await this.client.solana.buildSignAndSend(instruction);
                console.log(`[Launch] Job posted via Wallet. Signature: ${signature}`);
            }

            // Step C: If confidential, wait for RUNNING state and send real definition
            if (isConfidential) {
                console.log(`[Confidential] Job posted (${jobAddress}). Waiting for RUNNING state to send real definition...`);
                this.waitForRunningAndSendDefinition(jobAddress, jobDefinition, ipfsHash, deploymentUuid)
                    .catch(e => console.error(`[Confidential] Failed to handoff definition for ${jobAddress}:`, e));
            }

            this.sendAuditLog({
                action: "JOB_LAUNCHED",
                jobAddress,
                details: { ipfsHash, marketAddress, isConfidential, authMode: this.authMode, deploymentUuid }
            });

            return {
                status: "success",
                jobAddress: jobAddress,
                deploymentUuid: deploymentUuid,
                ipfsHash: ipfsHash,
            };
        } catch (error: any) {
            console.error("Launch Error:", error);
            throw new Error(`Nosana SDK Error: ${error.message}`);
        }
    }

    async waitForRunningAndSendDefinition(jobAddress: string, realJobDefinition: any, dummyIpfsHash: string, deploymentUuid?: string) {
        console.log(`[Confidential] Starting poll for job ${jobAddress}...`);
        const maxRetries = 600; // 10 minutes
        let job: any;
        const addr = address(jobAddress);

        for (let i = 0; i < maxRetries; i++) {
            try {
                // Use retry wrapper to handle 429s gracefully during polling
                job = await retry(() => this.client.jobs.get(addr), 3, 2000);

                if (job.state === JobState.RUNNING || (job.state as any) === 1) {
                    console.log(`[Confidential] Job ${jobAddress} is RUNNING on node ${job.node}. Sending definition...`);
                    break;
                }
                if (job.state === JobState.COMPLETED || job.state === JobState.STOPPED) {
                    console.warn(`[Confidential] Job ${jobAddress} ended before we could send definition.`);
                    return;
                }
            } catch (e) { }
            // Increase polling interval to 3s to reduce load
            await new Promise(r => setTimeout(r, 3000));
        }

        if (!job || (job.state !== JobState.RUNNING && (job.state as any) !== 1)) {
            console.error(`[Confidential] Timeout waiting for job ${jobAddress} to run.`);
            return;
        }

        try {
            let fetchHeaders: any = { 'Content-Type': 'application/json' };
            let walletAddress: string | undefined;

            if (this.authMode === 'api') {
                // Use the external signing API to get a signed message for node authentication
                console.log(`[Confidential] Requesting Auth Header from API for job ${jobAddress}...`);
                const apiAuth = await this.generateApiNodeAuthHeader();
                fetchHeaders['Authorization'] = apiAuth.header;
                walletAddress = apiAuth.userAddress;
                console.log(`[Confidential] Got API auth header for wallet: ${walletAddress}`);
            } else {
                const headers = await this.client.authorization.generateHeaders(dummyIpfsHash, { includeTime: true } as any);
                headers.forEach((value, key) => { fetchHeaders[key] = value; });
            }

            const domain = process.env.NOSANA_INGRESS_DOMAIN || "node.k8s.prd.nos.ci";
            const canonicalJobAddress = job.address.toString();
            const nodeUrl = `https://${job.node}.${domain}/job/${canonicalJobAddress}/job-definition`;

            console.log(`[Confidential] Posting definition to ${nodeUrl}...`);

            const sendDef = async (headers: any) => {
                const response = await fetch(nodeUrl, {
                    method: "POST",
                    headers,
                    body: JSON.stringify(realJobDefinition)
                });
                if (!response.ok) {
                    const text = await response.text();
                    throw { status: response.status, message: text };
                }
                return response;
            };

            try {
                await sendDef(fetchHeaders);
            } catch (e: any) {
                if (e.status >= 400 && e.status < 500) {
                    console.warn(`[Confidential] Node rejected definition (${e.status} - ${e.message}), retrying in 5s...`);
                    await new Promise(r => setTimeout(r, 5000));

                    // Regenerate headers - clear cache to force fresh signature
                    if (this.authMode === 'api') {
                        this.cachedApiAuth = null; // Clear cache to get fresh signature
                        const apiAuth = await this.generateApiNodeAuthHeader();
                        fetchHeaders['Authorization'] = apiAuth.header;
                    } else {
                        const newHeaders = await this.client.authorization.generateHeaders(dummyIpfsHash, { includeTime: true } as any);
                        newHeaders.forEach((value, key) => { fetchHeaders[key] = value; });
                    }

                    await sendDef(fetchHeaders);
                } else {
                    throw e;
                }
            }

            console.log(`[Confidential] Successfully handed off definition to node for job ${canonicalJobAddress}`);

            try {
                const services = getJobExposedServices(realJobDefinition, canonicalJobAddress);
                if (services && services.length > 0) {
                    const domain = process.env.NOSANA_INGRESS_DOMAIN || "node.k8s.prd.nos.ci";
                    const serviceUrl = `https://${services[0].hash}.${domain}`;
                    console.log(`[Confidential] Resolved Service URL from secret definition: ${serviceUrl}`);

                    const jobInfo = this.watchedJobs.get(jobAddress);
                    if (jobInfo) {
                        jobInfo.serviceUrl = serviceUrl;
                    }
                }
            } catch (err) {
                console.error(`[Confidential] Failed to resolve service URL from definition:`, err);
            }

        } catch (e: any) {
            console.error(`[Confidential] Failed to send definition to node:`, e.message || e);
        }
    }

    private async sendAuditLog(event: {
        action: string;
        jobAddress: string;
        details?: any;
        status?: string;
    }) {
        const apiGatewayUrl = process.env.API_GATEWAY_URL || "http://localhost:8000";
        const payload = {
            action: event.action,
            resource_type: "job",
            resource_id: event.jobAddress,
            details: event.details || {},
            status: event.status || "success",
        };

        try {
            await fetch(`${apiGatewayUrl}/audit/internal/log`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Internal-API-Key": process.env.INTERNAL_API_KEY || "dev-internal-key"
                },
                body: JSON.stringify(payload),
            });
        } catch (err) {
            console.error(`[audit] Failed to send audit log for ${event.action}:`, err);
        }
    }

    async stopJob(jobAddress: string) {
        try {
            console.log(`Attempting to stop job: ${jobAddress} (Mode: ${this.authMode})`);

            if (this.authMode === 'api') {
                // Use direct HTTP API: POST /api/jobs/{address}/stop
                console.log(`[API] Stopping job via HTTP API...`);
                const result = await this.apiRequest<{ tx: string; job: string; delisted: boolean }>(
                    `/jobs/${jobAddress}/stop`,
                    { method: 'POST' }
                );
                console.log(`Job ${jobAddress} stopped via API. TX: ${result.tx}, Delisted: ${result.delisted}`);

                this.sendAuditLog({
                    action: "JOB_STOPPED",
                    jobAddress,
                    details: { tx: result.tx, delisted: result.delisted, manual_stop: true, via: 'api' }
                });

                return { status: "stopped", txSignature: result.tx, delisted: result.delisted };
            } else {
                const addr = address(jobAddress);
                const job = await retry(() => this.client.jobs.get(addr));

                let instruction;
                if (job.state === JobState.RUNNING) {
                    instruction = await retry(() => this.client.jobs.end({ job: addr }));
                } else if (job.state === JobState.QUEUED) {
                    instruction = await retry(() => this.client.jobs.delist({ job: addr }));
                } else {
                    throw new Error(`Cannot stop job in state: ${job.state}`);
                }

                const signature = await retry(() => this.client.solana.buildSignAndSend(instruction));
                this.sendAuditLog({
                    action: "JOB_STOPPED",
                    jobAddress,
                    details: { signature, manual_stop: true }
                });

                return { status: "stopped", txSignature: signature };
            }
        } catch (error: any) {
            console.error("Stop Job Failed:", error);
            this.sendAuditLog({
                action: "JOB_STOP_FAILED",
                jobAddress,
                status: "error",
                details: { error: error.message }
            });
            throw new Error(`Stop Error: ${error.message}`);
        }
    }

    async extendJob(jobAddress: string, duration: number) {
        try {
            console.log(`Extending job ${jobAddress} by ${duration} seconds...`);
            const addr = address(jobAddress);

            if (this.authMode === 'api') {
                // Use direct HTTP API: POST /api/jobs/{address}/extend
                // Note: API expects "seconds" field in request body
                console.log(`[API] Extending job via HTTP API by ${duration} seconds...`);
                const result = await this.apiRequest<{ tx: string; job: string; credits: { costUSD: number; creditsUsed: number; reservationId: string } }>(
                    `/jobs/${jobAddress}/extend`,
                    {
                        method: 'POST',
                        body: { seconds: duration }
                    }
                );
                console.log(`Job ${jobAddress} extended via API. TX: ${result.tx}, Credits Used: ${result.credits.creditsUsed}`);

                this.sendAuditLog({
                    action: "JOB_EXTENDED",
                    jobAddress,
                    details: { duration, tx: result.tx, creditsUsed: result.credits.creditsUsed, via: 'api' }
                });

                return { status: "success", jobAddress, txSignature: result.tx, creditsUsed: result.credits.creditsUsed };
            } else {
                const instruction = await this.client.jobs.extend({
                    job: addr,
                    timeout: duration,
                });
                const signature = await this.client.solana.buildSignAndSend(instruction);

                this.sendAuditLog({
                    action: "JOB_EXTENDED",
                    jobAddress,
                    details: { duration, signature }
                });

                return { status: "success", jobAddress, txSignature: signature };
            }
        } catch (error: any) {
            console.error("Extend Error:", error);
            this.sendAuditLog({
                action: "JOB_EXTEND_FAILED",
                jobAddress,
                status: "error",
                details: { duration, error: error.message }
            });
            throw new Error(`Nosana SDK Error: ${error.message}`);
        }
    }

    async getLogStreamer() {
        if (this.authMode === 'api') {
            // For API mode, pass an auth provider function that uses the external signing API
            return new LogStreamer(null, () => this.generateApiNodeAuthHeader());
        } else {
            if (!this.client.wallet) throw new Error("Wallet not initialized");
            return new LogStreamer(this.client.wallet as any);
        }
    }

    async getJob(jobAddress: string) {
        try {
            const addr = address(jobAddress);
            const job = await retry(() => this.client.jobs.get(addr));
            const isRunning = job.state === JobState.RUNNING;
            let serviceUrl: string | null = null;

            const cachedJob = this.watchedJobs.get(jobAddress);
            if (cachedJob?.serviceUrl) {
                serviceUrl = cachedJob.serviceUrl;
            }

            if (isRunning && !serviceUrl && job.ipfsJob) {
                try {
                    const rawDef = await retry(() => this.client.ipfs.retrieve(job.ipfsJob!));
                    if (rawDef) {
                        const jobDefinition = rawDef as JobDefinition;
                        const services = getJobExposedServices(jobDefinition, jobAddress);
                        if (services && services.length > 0) {
                            const domain = process.env.NOSANA_INGRESS_DOMAIN || "node.k8s.prd.nos.ci";
                            serviceUrl = `https://${services[0].hash}.${domain}`;

                            if (cachedJob) {
                                cachedJob.serviceUrl = serviceUrl;
                            }
                        }
                    }
                } catch (e) {
                    console.error("Failed to resolve service URL:", e);
                }
            }

            return {
                status: "success",
                jobState: job.state,
                jobAddress: jobAddress,
                runAddress: job.project,
                nodeAddress: job.node,
                price: job.price.toString(),
                ipfsResult: job.ipfsResult,
                serviceUrl: serviceUrl,
            };
        } catch (error: any) {
            throw new Error(`Get Job Error: ${error.message}`);
        }
    }

    async getJobLogs(jobAddress: string) {
        try {
            const addr = address(jobAddress);
            const job = await retry(() => this.client.jobs.get(addr));

            if (!job.ipfsResult) {
                return { status: "pending", logs: ["Job is running or hasn't posted results yet."] };
            }

            const result = await retry(() => this.client.ipfs.retrieve(job.ipfsResult!));
            return { status: "completed", ipfsHash: job.ipfsResult, result: result };
        } catch (error: any) {
            if (error.message && error.message.includes("IPFS")) {
                console.log(`[Confidential] IPFS fetch failed. Attempting direct node retrieval for ${jobAddress}...`);
                return this.retrieveConfidentialResults(jobAddress);
            }
            console.error("Get Logs Error:", error);
            throw new Error(`Get Logs Error: ${error.message}`);
        }
    }

    async retrieveConfidentialResults(jobAddress: string) {
        try {
            const addr = address(jobAddress);
            const job = await this.client.jobs.get(addr);

            if (!job.ipfsJob) return { status: "pending", logs: ["Job has no IPFS hash."] };

            const dummyHash = job.ipfsJob;
            let fetchHeaders: any = {};

            if (this.authMode === 'api') {
                // Use the external signing API to get a signed message for node authentication
                console.log(`[Confidential] Requesting Auth Header from API for results retrieval...`);
                const apiAuth = await this.generateApiNodeAuthHeader();
                fetchHeaders['Authorization'] = apiAuth.header;
            } else {
                const headers = await this.client.authorization.generateHeaders(dummyHash, { includeTime: true } as any);
                headers.forEach((value, key) => { fetchHeaders[key] = value; });
            }

            const domain = process.env.NOSANA_INGRESS_DOMAIN || "node.k8s.prd.nos.ci";
            const nodeUrl = `https://${job.node}.${domain}/job/${jobAddress}/results`;

            console.log(`[Confidential] Fetching results from ${nodeUrl}...`);
            const response = await fetch(nodeUrl, {
                method: "GET",
                headers: fetchHeaders
            });

            if (!response.ok) {
                throw new Error(`Node rejected result fetch: ${response.status} ${await response.text()}`);
            }

            const results = await response.json();
            return { status: "completed", isConfidential: true, result: results };
        } catch (e: any) {
            console.error(`[Confidential] Failed to retrieve results:`, e);
            return { status: "error", logs: [`Failed to retrieve confidential results: ${e.message}`] };
        }
    }

    async getBalance() {
        if (this.authMode === 'api') {
            try {
                // Use direct API call for credits balance
                // GET /api/credits/balance returns: { assignedCredits, reservedCredits, settledCredits }
                const balance = await this.apiRequest<{
                    assignedCredits: number;
                    reservedCredits: number;
                    settledCredits: number;
                }>('/credits/balance');

                // Calculate available credits: assigned - reserved - settled
                const availableCredits = balance.assignedCredits - balance.reservedCredits - balance.settledCredits;

                return {
                    sol: 0,
                    nos: availableCredits.toFixed(2),
                    assignedCredits: balance.assignedCredits,
                    reservedCredits: balance.reservedCredits,
                    settledCredits: balance.settledCredits,
                    address: "API_ACCOUNT"
                };
            } catch (error: any) {
                console.error('[API] Failed to get balance:', error.message);
                // Fallback to SDK if direct API fails
                try {
                    const balance = await this.client.api.credits.balance();
                    return {
                        sol: 0,
                        nos: (balance as any).amount || "0",
                        address: "API_ACCOUNT"
                    };
                } catch (e) {
                    throw error; // Re-throw original error
                }
            }
        }
        const sol = await this.client.solana.getBalance();
        const nos = await this.client.nos.getBalance();
        return {
            sol: sol,
            nos: nos.toString() || "0",
            address: this.client.wallet ? this.client.wallet.address : "Unknown",
        };
    }

    async recoverJobs() {
        if (this.authMode === 'api') {
            console.log("[Job Recovery] Attempting to recover jobs for API mode...");
            try {
                // API mode recovery is limited because the API doesn't expose a "list my jobs/deployments" endpoint
                // For now, we can only recover jobs that are still in the watchedJobs cache
                // Full recovery would require persisting job addresses externally (e.g., in a database)

                // Check any known watched jobs that might have crashed
                for (const [jobAddress, jobInfo] of this.watchedJobs.entries()) {
                    try {
                        const jobStatus = await this.getJob(jobAddress);
                        if ((jobStatus.jobState === JobState.RUNNING || (jobStatus.jobState as any) === 1)) {
                            console.log(`[Recovery] Job ${jobAddress} is still running - watchdog should be active`);
                        } else {
                            console.log(`[Recovery] Job ${jobAddress} is no longer running (state: ${jobStatus.jobState})`);
                            // Remove from watched jobs if it's terminated
                            this.watchedJobs.delete(jobAddress);
                        }
                    } catch (e) {
                        console.warn(`[Recovery] Could not check job ${jobAddress}:`, e);
                    }
                }

                console.log("[Job Recovery] API mode recovery complete (limited to cached jobs)");
                console.log("[Job Recovery] Note: For full recovery, persist job addresses externally");
            } catch (e: any) {
                console.error("[Job Recovery] Failed to recover jobs in API mode:", e);
            }
            return;
        }
        if (!this.client.wallet) return;
        try {
            const jobs = await retry(() => this.client.jobs.all());
            const myAddress = this.client.wallet.address.toString();
            const myJobs = jobs.filter((j: any) => j.project?.toString() === myAddress);

            for (const job of myJobs) {
                const jobAddress = job.address.toString();
                const state = job.state;
                if (((state as any) === JobState.RUNNING || (state as any) === 1) && !this.watchedJobs.has(jobAddress)) {
                    console.log(`Recovering watchdog for running job: ${jobAddress}`);
                    this.watchJob(jobAddress, process.env.ORCHESTRATOR_URL || "http://localhost:8080", {
                        isConfidential: true,
                        resources_allocated: { gpu_allocated: 1, vcpu_allocated: 8, ram_gb_allocated: 32 }
                    });
                }
            }
        } catch (e: any) {
            console.error("Failed to recover jobs:", e);
        }
    }

    async watchJob(
        jobAddress: string,
        orchestratorUrl: string,
        options?: {
            jobDefinition?: any;
            marketAddress?: string;
            deploymentUuid?: string;
            isConfidential?: boolean;
            resources_allocated?: {
                gpu_allocated: number;
                vcpu_allocated: number;
                ram_gb_allocated: number;
            };
            credentialName?: string;
        }
    ) {
        const now = Date.now();

        const resources = options?.resources_allocated || {
            gpu_allocated: 1,
            vcpu_allocated: 8,
            ram_gb_allocated: 32
        };

        const jobInfo: WatchedJobInfo = {
            jobAddress,
            deploymentUuid: options?.deploymentUuid,
            startTime: now,
            lastExtendTime: now,
            jobDefinition: options?.jobDefinition || null,
            marketAddress: options?.marketAddress || "",
            isConfidential: options?.isConfidential !== undefined ? options.isConfidential : true,
            resources,
            userStopped: false,
            credentialName: options?.credentialName,
        };
        this.watchedJobs.set(jobAddress, jobInfo);

        let lastState: JobState | null = null;
        let lastHeartbeat = 0;

        console.log(`[watchdog] Started watching job: ${jobAddress}`);

        this.sendAuditLog({
            action: "WATCHDOG_STARTED",
            jobAddress,
            details: { resources, deploymentUuid: options?.deploymentUuid }
        });

        while (true) {
            try {
                const currentTime = Date.now();
                const job = await this.getJob(jobAddress);
                const currentJobInfo = this.watchedJobs.get(jobAddress);

                if (!currentJobInfo) {
                    console.log(`[watchdog] Job ${jobAddress} removed from watch list, stopping loop`);
                    return;
                }

                if (job.jobState !== lastState) {
                    console.log(`[watchdog] Job state changed: ${lastState} -> ${job.jobState} for ${jobAddress}`);

                    this.sendAuditLog({
                        action: "JOB_STATE_CHANGED",
                        jobAddress,
                        details: { old_state: lastState, new_state: job.jobState }
                    });

                    lastState = job.jobState;
                }

                // Auto-Extend
                if ((job.jobState as any) === JobState.RUNNING || (job.jobState as any) === 1) {
                    const timeSinceLastExtend = currentTime - currentJobInfo.lastExtendTime;
                    const timeUntilTimeout = JOB_TIMEOUT_MS - timeSinceLastExtend;

                    if (timeUntilTimeout <= EXTEND_THRESHOLD_MS && timeUntilTimeout > 0) {
                        console.log(`[auto-extend] Job ${jobAddress} low time, extending...`);
                        try {
                            await this.extendJob(jobAddress, EXTEND_DURATION_SECS);
                            currentJobInfo.lastExtendTime = currentTime;
                            console.log(`[auto-extend] Successfully extended job ${jobAddress}`);

                            this.sendAuditLog({
                                action: "JOB_AUTO_EXTENDED",
                                jobAddress,
                                details: { duration: EXTEND_DURATION_SECS }
                            });
                        } catch (extendErr: any) {
                            console.error(`[auto-extend] Failed to extend job ${jobAddress}:`, extendErr);
                            this.sendAuditLog({
                                action: "JOB_AUTO_EXTEND_FAILED",
                                jobAddress,
                                status: "error",
                                details: { error: extendErr.message }
                            });
                        }
                    }

                    // Heartbeat
                    if (currentTime - lastHeartbeat > 30000) {
                        try {
                            const payload = {
                                provider: "nosana",
                                provider_instance_id: jobAddress,
                                gpu_allocated: currentJobInfo.resources.gpu_allocated,
                                vcpu_allocated: currentJobInfo.resources.vcpu_allocated,
                                ram_gb_allocated: currentJobInfo.resources.ram_gb_allocated,
                                health_score: 100,
                                state: "ready",
                                expose_url: job.serviceUrl,
                            };
                            await fetch(`${orchestratorUrl}/inventory/heartbeat`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(payload),
                            });
                            lastHeartbeat = currentTime;
                        } catch (err) {
                            console.error(`[heartbeat] Failed to send heartbeat for ${jobAddress}:`, err);
                        }
                    }
                }

                // Termination
                const state = job.jobState as any;
                const isTerminated =
                    state === JobState.COMPLETED ||
                    state === 2 ||
                    state === JobState.STOPPED ||
                    state === 3 ||
                    state === 4;

                if (isTerminated) {
                    const runtime = currentTime - currentJobInfo.startTime;
                    const runtimeMins = Math.round(runtime / 60000);
                    console.log(`[watchdog] Job ${jobAddress} ended (state: ${job.jobState}) after ${runtimeMins} min`);

                    this.sendAuditLog({
                        action: "WATCHDOG_TERMINATED",
                        jobAddress,
                        details: {
                            final_state: state,
                            runtime_mins: runtimeMins,
                            user_stopped: currentJobInfo.userStopped
                        }
                    });

                    const shouldRedeploy =
                        !currentJobInfo.userStopped &&
                        currentJobInfo.jobDefinition &&
                        currentJobInfo.marketAddress &&
                        runtime >= MIN_RUNTIME_FOR_REDEPLOY_MS;

                    const tooShort = runtime < MIN_RUNTIME_FOR_REDEPLOY_MS;

                    if (currentJobInfo.userStopped) {
                    } else if (tooShort) {
                        try {
                            const payload = {
                                provider: "nosana",
                                provider_instance_id: jobAddress,
                                gpu_allocated: 0,
                                vcpu_allocated: 0,
                                ram_gb_allocated: 0,
                                health_score: 0,
                                state: "failed",
                            };
                            await fetch(`${orchestratorUrl}/inventory/heartbeat`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(payload),
                            });
                        } catch (err) { }
                    } else if (shouldRedeploy) {
                        console.log(`[auto-redeploy] Attempting redeploy for ${jobAddress}...`);
                        try {
                            const newJob = await this.launchJob(
                                currentJobInfo.jobDefinition,
                                currentJobInfo.marketAddress,
                                currentJobInfo.isConfidential
                            );

                            try {
                                const updatePayload = {
                                    provider: "nosana",
                                    provider_instance_id: newJob.jobAddress,
                                    old_provider_instance_id: jobAddress,
                                    gpu_allocated: currentJobInfo.resources.gpu_allocated,
                                    vcpu_allocated: currentJobInfo.resources.vcpu_allocated,
                                    ram_gb_allocated: currentJobInfo.resources.ram_gb_allocated,
                                    health_score: 50,
                                    state: "provisioning",
                                };
                                await fetch(`${orchestratorUrl}/inventory/heartbeat`, {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify(updatePayload),
                                });
                            } catch (err) { }

                            this.watchJob(newJob.jobAddress, orchestratorUrl, {
                                jobDefinition: currentJobInfo.jobDefinition,
                                marketAddress: currentJobInfo.marketAddress,
                                isConfidential: currentJobInfo.isConfidential,
                                deploymentUuid: newJob.deploymentUuid,
                                resources_allocated: currentJobInfo.resources,
                            });
                        } catch (redeployErr: any) {
                            console.error(`[auto-redeploy] Failed:`, redeployErr);
                            try {
                                const payload = {
                                    provider: "nosana",
                                    provider_instance_id: jobAddress,
                                    gpu_allocated: 0,
                                    vcpu_allocated: 0,
                                    ram_gb_allocated: 0,
                                    health_score: 0,
                                    state: "failed",
                                };
                                await fetch(`${orchestratorUrl}/inventory/heartbeat`, {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify(payload),
                                });
                            } catch (err) { }
                        }
                    }

                    try {
                        const payload = {
                            provider: "nosana",
                            provider_instance_id: jobAddress,
                            gpu_allocated: 0,
                            vcpu_allocated: 0,
                            ram_gb_allocated: 0,
                            health_score: 0,
                            state: "terminated",
                        };
                        await fetch(`${orchestratorUrl}/inventory/heartbeat`, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify(payload),
                        });
                    } catch (err) { }

                    this.watchedJobs.delete(jobAddress);
                    return;
                }

            } catch (error) {
                console.error(`[watchdog] Error loop ${jobAddress}:`, error);
            }

            await new Promise((r) => setTimeout(r, 60000));
        }
    }

    private startWatchdogSummary() {
        if (this.summaryInterval) {
            setInterval(() => {
                this.logWatchdogSummary();
            }, this.summaryInterval);
        }
    }

    private logWatchdogSummary() {
        const total = this.watchedJobs.size;
        if (total > 0) {
            console.log(`[watchdog-summary] Currently watching ${total} jobs.`);
        }
    }

    /**
     * Get all watched job addresses that use a specific credential.
     * Used when a credential is deleted to warn about running jobs.
     */
    getWatchedJobsByCredential(credentialName: string): string[] {
        const jobAddresses: string[] = [];
        for (const [jobAddress, jobInfo] of this.watchedJobs.entries()) {
            if (jobInfo.credentialName === credentialName) {
                jobAddresses.push(jobAddress);
            }
        }
        return jobAddresses;
    }

    /**
     * Get all watched job addresses.
     */
    getAllWatchedJobs(): string[] {
        return Array.from(this.watchedJobs.keys());
    }

    /**
     * Mark a job as user-stopped to prevent auto-redeploy.
     * Called when a credential is deleted and we want to gracefully stop jobs.
     */
    markJobForNoRedeploy(jobAddress: string): void {
        const jobInfo = this.watchedJobs.get(jobAddress);
        if (jobInfo) {
            jobInfo.userStopped = true;
            console.log(`[watchdog] Job ${jobAddress} marked to skip auto-redeploy`);
        }
    }
}
