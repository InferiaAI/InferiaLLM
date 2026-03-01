import { createNosanaClient, NosanaClient, NosanaNetwork, getJobExposedServices, JobState } from '@nosana/kit';
import { address, createKeyPairSignerFromBytes } from '@solana/kit';
import bs58 from 'bs58';
import type { JobDefinition } from '@nosana/types';
import { LogStreamer } from './nosana_logs';

// Deployment timing constants (in milliseconds)
const DEPLOYMENT_POLL_INTERVAL_MS = 10000;
const DEPLOYMENT_START_TIMEOUT_MS = 5 * 60 * 1000; // 5 mins to wait for RUNNING
const MIN_RUNTIME_FOR_REDEPLOY_MS = 20 * 60 * 1000;

// Nosana Dashboard API constants
const NOSANA_API_BASE_URL = process.env.NOSANA_API_URL || 'https://dashboard.k8s.prd.nos.ci/api';
const SIGN_MESSAGE = 'Hello Nosana Node!';

// Deployment strategy types (from Swagger schema)
type DeploymentStrategy = 'SIMPLE' | 'SIMPLE-EXTEND' | 'SCHEDULED' | 'INFINITE';
type DeploymentStatus = 'DRAFT' | 'ERROR' | 'STARTING' | 'RUNNING' | 'STOPPING' | 'STOPPED' | 'INSUFFICIENT_FUNDS' | 'ARCHIVED';

interface DeploymentEndpoint {
    opId: string;
    port: number | string;
    url: string;
}

interface DeploymentResponse {
    id: string;
    name: string;
    vault: string;
    market: string;
    owner: string;
    status: DeploymentStatus;
    strategy: DeploymentStrategy;
    replicas: number;
    timeout: number;
    endpoints: DeploymentEndpoint[];
    confidential: boolean;
    active_revision: number;
    active_jobs: number;
    created_at: string;
    updated_at: string;
    rotation_time?: number;
    schedule?: string;
}

interface DeploymentJobResponse {
    tx: string;
    job: string;
    deployment: string;
    market: string;
    revision: number;
    state: 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'STOPPED';
    time_start: number;
    created_at: string;
    updated_at: string;
}

interface WatchedDeploymentInfo {
    deploymentId: string;          // The deployment ID (primary key)
    jobAddresses: string[];        // Active job addresses within the deployment
    startTime: number;
    jobDefinition: any;
    marketAddress: string;
    isConfidential?: boolean;
    strategy: DeploymentStrategy;
    resources: {
        gpu_allocated: number;
        vcpu_allocated: number;
        ram_gb_allocated: number;
    };
    userStopped: boolean;
    serviceUrl?: string;
    credentialName?: string;       // Which credential was used for this deployment
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
    private watchedDeployments = new Map<string, WatchedDeploymentInfo>();
    private summaryInterval: number = 60000;
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
     * Authenticate with the Nosana API to sign a message for node communication
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
        const result = await this.apiRequest<{ signature: string; message: string; userAddress: string }>('/auth/sign-message/external', {
            method: 'POST',
            body: { message }
        });

        // Store in cache
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
     * Get deployment details from the Nosana API
     * GET /api/deployments/{deployment}
     */
    async getDeployment(deploymentId: string): Promise<DeploymentResponse> {
        return this.apiRequest<DeploymentResponse>(`/deployments/${deploymentId}`);
    }

    /**
     * Get jobs for a deployment
     * GET /api/deployments/{deployment}/jobs
     */
    async getDeploymentJobs(deploymentId: string, state?: string): Promise<{
        jobs: DeploymentJobResponse[];
        pagination: { cursor_next: string | null; cursor_prev: string | null; total_items: number };
    }> {
        const params = state ? `?state=${state}` : '';
        return this.apiRequest(`/deployments/${deploymentId}/jobs${params}`);
    }

    /**
     * Get a specific job within a deployment
     * GET /api/deployments/{deployment}/jobs/{job}
     */
    async getDeploymentJob(deploymentId: string, jobAddress: string): Promise<{
        confidential: boolean;
        revision: number;
        market: string;
        node: string;
        state: string | number;
        jobStatus: string | null;
        jobDefinition: any;
        jobResult: any | null;
        timeStart: number;
        timeEnd: number;
        listedAt: number;
    }> {
        return this.apiRequest(`/deployments/${deploymentId}/jobs/${jobAddress}`);
    }

    /**
     * Get job details from the legacy Nosana Dashboard API (for backward compat)
     * GET /api/jobs/{address}
     */
    async getJobFromApi(jobAddress: string): Promise<{
        ipfsJob: string;
        ipfsResult: string | null;
        market: string;
        node: string;
        payer: string;
        price: number;
        project: string;
        state: number;
        jobDefinition: any;
        jobResult: any | null;
        jobStatus: string | null;
        timeStart: number;
        timeEnd: number;
        timeout: number;
    } | null> {
        if (this.authMode !== 'api') {
            return null;
        }

        try {
            return await this.apiRequest(`/jobs/${jobAddress}`);
        } catch (error: any) {
            console.warn(`[API] Failed to get job details for ${jobAddress}:`, error.message);
            return null;
        }
    }

    markDeploymentAsStopping(deploymentId: string): void {
        const info = this.watchedDeployments.get(deploymentId);
        if (info) {
            info.userStopped = true;
            console.log(`[user-stop] Marked deployment ${deploymentId} as user-stopped`);
        }
    }

    /**
     * @deprecated Use markDeploymentAsStopping instead. Kept for backward compat.
     */
    markJobAsStopping(jobOrDeploymentId: string): void {
        // Try deployment first
        if (this.watchedDeployments.has(jobOrDeploymentId)) {
            this.markDeploymentAsStopping(jobOrDeploymentId);
            return;
        }
        // Search by job address
        for (const [depId, info] of this.watchedDeployments.entries()) {
            if (info.jobAddresses.includes(jobOrDeploymentId)) {
                info.userStopped = true;
                console.log(`[user-stop] Marked deployment ${depId} (via job ${jobOrDeploymentId}) as user-stopped`);
                return;
            }
        }
        console.warn(`[user-stop] No deployment found for ${jobOrDeploymentId}`);
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

    /**
     * Launch a deployment using the Nosana Deployments API.
     * 
     * Flow:
     * 1. POST /api/deployments/create   → creates deployment in DRAFT status
     * 2. POST /api/deployments/{id}/start → transitions to STARTING
     * 3. Poll GET /api/deployments/{id}   → wait for RUNNING, get endpoints & jobs
     */
    async launchJob(jobDefinition: any, marketAddress: string, isConfidential: boolean = true) {
        try {
            let deploymentId = "unknown";
            let jobAddress = "unknown";
            let serviceUrl: string | undefined;

            if (this.authMode === 'api') {
                console.log(`[Launch] Creating deployment via API in market: ${marketAddress} (confidential: ${isConfidential})`);

                // Step 1: Create deployment (returns in DRAFT state)
                console.log(`[Launch] Step 1: POST /api/deployments/create...`);
                const deployment = await this.apiRequest<DeploymentResponse>('/deployments/create', {
                    method: 'POST',
                    body: {
                        name: `inferia-${Date.now()}`,
                        market: marketAddress,
                        job_definition: jobDefinition,
                        replicas: 1,
                        timeout: 60,                      // 60 minutes (API expects minutes)
                        strategy: 'SIMPLE-EXTEND' as DeploymentStrategy, // Auto-extends jobs
                        confidential: isConfidential,
                    }
                });

                deploymentId = deployment.id;
                console.log(`[Launch] Deployment created: ${deploymentId} (status: ${deployment.status})`);

                // Step 2: Start the deployment
                console.log(`[Launch] Step 2: POST /api/deployments/${deploymentId}/start...`);
                const startResult = await this.apiRequest<{ status: string; updated_at: string }>(
                    `/deployments/${deploymentId}/start`,
                    { method: 'POST' }
                );
                console.log(`[Launch] Deployment starting: ${startResult.status}`);

                // Step 3: Poll for RUNNING status and get job+endpoint info
                console.log(`[Launch] Step 3: Polling for deployment to reach RUNNING status...`);
                const startPollTime = Date.now();

                while (Date.now() - startPollTime < DEPLOYMENT_START_TIMEOUT_MS) {
                    await new Promise(r => setTimeout(r, DEPLOYMENT_POLL_INTERVAL_MS));

                    const status = await this.getDeployment(deploymentId);
                    console.log(`[Launch] Deployment ${deploymentId} status: ${status.status}`);

                    if (status.status === 'RUNNING') {
                        // Get endpoints (service URLs)
                        if (status.endpoints && status.endpoints.length > 0) {
                            serviceUrl = status.endpoints[0].url;
                            console.log(`[Launch] Service URL: ${serviceUrl}`);
                        }

                        // Get job addresses
                        try {
                            const jobsResult = await this.getDeploymentJobs(deploymentId, 'RUNNING');
                            if (jobsResult.jobs.length > 0) {
                                jobAddress = jobsResult.jobs[0].job;
                                console.log(`[Launch] Active job: ${jobAddress}`);
                            }
                        } catch (e) {
                            console.warn(`[Launch] Could not fetch jobs for deployment:`, e);
                        }

                        break;
                    }

                    if (status.status === 'ERROR' || status.status === 'STOPPED') {
                        throw new Error(`Deployment ${deploymentId} failed with status: ${status.status}`);
                    }

                    if (status.status === 'INSUFFICIENT_FUNDS') {
                        throw new Error(`Deployment ${deploymentId} failed: insufficient funds`);
                    }
                }

                // If we never saw RUNNING, fall through with whatever we have
                if (jobAddress === "unknown") {
                    console.warn(`[Launch] Did not resolve a running job within timeout, using deployment ID as reference`);
                }

                console.log(`[Launch] Deployment ${deploymentId} launched. Job: ${jobAddress}`);

                // If confidential, we need to wait for it to be RUNNING and send the definition
                if (isConfidential && jobAddress !== "unknown") {
                    const ipfsHash = jobDefinition?.ipfsHash || "dummy";
                    this.waitForRunningAndSendDefinition(jobAddress, jobDefinition, ipfsHash, deploymentId);
                }

            } else {
                // Wallet Mode: Use SDK
                console.log("[Launch] Using wallet mode...");

                // Use SDK deployments API
                const deployment = await this.client.api.deployments.create({
                    name: `inferia-${Date.now()}`,
                    market: marketAddress,
                    job_definition: jobDefinition,
                    replicas: 1,
                    timeout: 60,          // minutes
                    strategy: 'SIMPLE-EXTEND',
                    confidential: isConfidential,
                } as any);

                deploymentId = (deployment as any).id || (deployment as any).uuid;
                console.log(`[Launch] Deployment created: ${deploymentId}. Starting...`);

                // Start the deployment
                const depObj = await this.client.api.deployments.get(deploymentId);
                await (depObj as any).start();

                // Poll for Job Address
                let attempts = 0;
                while (attempts < 30) {
                    const statusObj = await this.client.api.deployments.get(deploymentId);
                    const statusAny = statusObj as any;

                    if (statusAny.status === 'RUNNING') {
                        // Get endpoints
                        if (statusAny.endpoints && statusAny.endpoints.length > 0) {
                            serviceUrl = statusAny.endpoints[0].url;
                        }

                        // Try to get jobs
                        try {
                            const jobs = statusAny.jobs || [];
                            if (jobs.length > 0) {
                                jobAddress = jobs[0].job || jobs[0].address;
                                console.log(`[Launch] Resolved Job Address: ${jobAddress}`);
                                break;
                            }
                        } catch (e) {
                            // Continue polling
                        }
                    }

                    if (statusAny.status === 'ERROR' || statusAny.status === 'STOPPED') {
                        throw new Error(`Deployment failed with status: ${statusAny.status}`);
                    }

                    await new Promise(r => setTimeout(r, 2000));
                    attempts++;
                }

                if (jobAddress === "unknown") {
                    console.warn("Could not resolve Job Address, using deployment ID as reference");
                }

                // If confidential, we need to wait for it to be RUNNING and send the definition
                if (isConfidential && jobAddress !== "unknown") {
                    const ipfsHash = jobDefinition?.ipfsHash || "dummy";
                    this.waitForRunningAndSendDefinition(jobAddress, jobDefinition, ipfsHash, deploymentId);
                }
            }

            this.sendAuditLog({
                action: "DEPLOYMENT_LAUNCHED",
                jobAddress: deploymentId,
                details: { deploymentId, jobAddress, marketAddress, isConfidential, authMode: this.authMode, serviceUrl }
            });

            return {
                status: "success",
                jobAddress: jobAddress !== "unknown" ? jobAddress : deploymentId,
                deploymentId: deploymentId,
                deploymentUuid: deploymentId,  // backward compat
                serviceUrl: serviceUrl,
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
                    console.warn(`[Confidential] Job ${jobAddress} ended before we could send definition. State: ${job.state}`);
                    return;
                }
            } catch (e: any) {
                console.debug(`[Confidential] Retry poll error for job ${jobAddress}: ${e.message || 'unknown error'}`);
            }
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

                    const depInfo = this.findDeploymentByJobAddress(jobAddress);
                    if (depInfo) {
                        depInfo.serviceUrl = serviceUrl;
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
            resource_type: "deployment",
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

    /**
     * Stop a deployment.
     * Uses POST /api/deployments/{deployment}/stop
     * Falls back to job-level stop for wallet mode.
     */
    async stopJob(jobOrDeploymentId: string) {
        try {
            console.log(`Attempting to stop: ${jobOrDeploymentId} (Mode: ${this.authMode})`);

            if (this.authMode === 'api') {
                // Determine if this is a deployment ID or a job address
                // Try deployment-level stop first
                let deploymentId = jobOrDeploymentId;

                // Check if this is a job address → find its deployment
                const depInfo = this.findDeploymentByJobAddress(jobOrDeploymentId);
                if (depInfo) {
                    deploymentId = depInfo.deploymentId;
                }

                console.log(`[API] Stopping deployment ${deploymentId} via POST /api/deployments/${deploymentId}/stop...`);

                try {
                    const result = await this.apiRequest<{ status: string; updated_at: string }>(
                        `/deployments/${deploymentId}/stop`,
                        { method: 'POST' }
                    );
                    console.log(`Deployment ${deploymentId} stopping. Status: ${result.status}`);

                    this.sendAuditLog({
                        action: "DEPLOYMENT_STOPPED",
                        jobAddress: deploymentId,
                        details: { status: result.status, manual_stop: true, via: 'deployments-api' }
                    });

                    return { status: "stopped", deploymentId, deploymentStatus: result.status };
                } catch (depError: any) {
                    // Fallback: If deployment-level stop fails, try job-level stop
                    console.warn(`[API] Deployment-level stop failed, trying job-level stop: ${depError.message}`);
                    const result = await this.apiRequest<{ tx: string; job: string; delisted: boolean }>(
                        `/jobs/${jobOrDeploymentId}/stop`,
                        { method: 'POST' }
                    );
                    console.log(`Job ${jobOrDeploymentId} stopped via legacy API. TX: ${result.tx}`);

                    return { status: "stopped", txSignature: result.tx, delisted: result.delisted };
                }
            } else {
                const addr = address(jobOrDeploymentId);
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
                    jobAddress: jobOrDeploymentId,
                    details: { signature, manual_stop: true }
                });

                return { status: "stopped", txSignature: signature };
            }
        } catch (error: any) {
            console.error("Stop Failed:", error);
            this.sendAuditLog({
                action: "STOP_FAILED",
                jobAddress: jobOrDeploymentId,
                status: "error",
                details: { error: error.message }
            });
            throw new Error(`Stop Error: ${error.message}`);
        }
    }

    /**
     * Extend a job/deployment.
     * For API mode with SIMPLE-EXTEND strategy, extensions are handled automatically.
     * For manual extend, we update the deployment timeout.
     * Falls back to job-level extend for backward compat.
     */
    async extendJob(jobOrDeploymentId: string, duration: number) {
        try {
            console.log(`Extending ${jobOrDeploymentId} by ${duration} seconds...`);

            if (this.authMode === 'api') {
                // Try deployment-level timeout update first
                let deploymentId = jobOrDeploymentId;

                const depInfo = this.findDeploymentByJobAddress(jobOrDeploymentId);
                if (depInfo) {
                    deploymentId = depInfo.deploymentId;
                }

                // First try deployment-level timeout update
                try {
                    const timeoutMinutes = Math.max(60, Math.ceil(duration / 60));
                    console.log(`[API] Updating deployment timeout to ${timeoutMinutes} minutes...`);
                    const result = await this.apiRequest<{ timeout: number; updated_at: string }>(
                        `/deployments/${deploymentId}/update-timeout`,
                        {
                            method: 'PATCH',
                            body: { timeout: timeoutMinutes }
                        }
                    );
                    console.log(`Deployment ${deploymentId} timeout updated to ${result.timeout} minutes`);

                    this.sendAuditLog({
                        action: "DEPLOYMENT_TIMEOUT_UPDATED",
                        jobAddress: deploymentId,
                        details: { timeoutMinutes: result.timeout, via: 'deployments-api' }
                    });

                    return { status: "success", deploymentId, timeout: result.timeout };
                } catch (depError: any) {
                    // Fallback to job-level extend
                    console.warn(`[API] Deployment timeout update failed, trying job-level extend: ${depError.message}`);
                    const result = await this.apiRequest<{ tx: string; job: string; credits: { costUSD: number; creditsUsed: number; reservationId: string } }>(
                        `/jobs/${jobOrDeploymentId}/extend`,
                        {
                            method: 'POST',
                            body: { seconds: duration }
                        }
                    );
                    console.log(`Job ${jobOrDeploymentId} extended via legacy API. TX: ${result.tx}`);

                    return { status: "success", jobAddress: jobOrDeploymentId, txSignature: result.tx, creditsUsed: result.credits.creditsUsed };
                }
            } else {
                const addr = address(jobOrDeploymentId);
                const instruction = await this.client.jobs.extend({
                    job: addr,
                    timeout: duration,
                });
                const signature = await this.client.solana.buildSignAndSend(instruction);

                this.sendAuditLog({
                    action: "JOB_EXTENDED",
                    jobAddress: jobOrDeploymentId,
                    details: { duration, signature }
                });

                return { status: "success", jobAddress: jobOrDeploymentId, txSignature: signature };
            }
        } catch (error: any) {
            console.error("Extend Error:", error);
            this.sendAuditLog({
                action: "EXTEND_FAILED",
                jobAddress: jobOrDeploymentId,
                status: "error",
                details: { duration, error: error.message }
            });
            throw new Error(`Nosana SDK Error: ${error.message}`);
        }
    }

    async getLogStreamer() {
        if (this.authMode === 'api') {
            console.log("[LogStreamer] Using API mode - creating ephemeral log streamer");
            return new LogStreamer();
        } else {
            if (!this.client.wallet) throw new Error("Wallet not initialized");
            return new LogStreamer(this.client.wallet as any);
        }
    }

    /**
     * Get job/deployment status.
     * In API mode, uses the Deployments API to get deployment status + endpoint URLs.
     * Supports both deployment IDs and job addresses.
     */
    async getJob(jobOrDeploymentId: string) {
        try {
            if (this.authMode === 'api') {
                // Try to resolve which deployment this belongs to
                let deploymentId: string | null = null;
                const depInfo = this.findDeploymentByJobAddress(jobOrDeploymentId);

                if (depInfo) {
                    deploymentId = depInfo.deploymentId;
                }

                // Try deployment-level status if we know the deployment ID
                // OR if it's formatted like a deployment UUID (less than 43 chars)
                if (deploymentId || jobOrDeploymentId.length < 43) {
                    const idToQuery = deploymentId || jobOrDeploymentId;

                    try {
                        const deployment = await this.getDeployment(idToQuery);

                        // Get the first running job address if any
                        let activeJobAddress: string | undefined;
                        let nodeAddress: string | undefined;

                        try {
                            const jobsResult = await this.getDeploymentJobs(idToQuery);
                            if (jobsResult.jobs.length > 0) {
                                const latestJob = jobsResult.jobs[0];
                                activeJobAddress = latestJob.job;
                                try {
                                    const jobDetail = await this.apiRequest<any>(`/deployments/${idToQuery}/jobs/${activeJobAddress}`);
                                    if (jobDetail && jobDetail.node) {
                                        nodeAddress = jobDetail.node;
                                    }
                                } catch (err) { }
                            }
                        } catch (e) {
                            // Jobs might not be available yet
                        }

                        // Map deployment status to a job-like state for backward compat
                        let jobState: any = deployment.status;
                        if (deployment.status === 'RUNNING') jobState = JobState.RUNNING;
                        else if (deployment.status === 'STOPPED' || deployment.status === 'STOPPING') jobState = JobState.STOPPED;
                        else if (deployment.status === 'STARTING') jobState = JobState.QUEUED;

                        // Service URL from deployment endpoints
                        let serviceUrl: string | null = null;
                        if (deployment.endpoints && deployment.endpoints.length > 0) {
                            serviceUrl = deployment.endpoints[0].url;
                        }

                        // Update cached service URL
                        const watchedDep = this.watchedDeployments.get(idToQuery);
                        if (watchedDep && serviceUrl) {
                            watchedDep.serviceUrl = serviceUrl;
                        }

                        return {
                            status: "success",
                            jobState: jobState,
                            jobAddress: activeJobAddress || idToQuery,
                            deploymentId: idToQuery,
                            deploymentStatus: deployment.status,
                            runAddress: deployment.owner,
                            nodeAddress: nodeAddress || "",
                            price: "0",
                            ipfsResult: null,
                            serviceUrl: serviceUrl,
                            endpoints: deployment.endpoints,
                        };
                    } catch (depError: any) {
                        // Suppress expected 401/404 errors during fallback to avoid console noise
                        if (!depError.message?.includes('401') && !depError.message?.includes('404')) {
                            console.warn(`[getJob] Deployment query failed for ${idToQuery}: ${depError.message}`);
                        }
                    }
                }

                // Fallback: Use legacy job API
                const jobDetails = await this.getJobFromApi(jobOrDeploymentId);
                if (jobDetails) {
                    let jobState: any = jobDetails.state;
                    if (jobDetails.state === 1) jobState = JobState.RUNNING;
                    else if (jobDetails.state === 0) jobState = JobState.QUEUED;
                    else if (jobDetails.state === 2) jobState = JobState.COMPLETED;

                    return {
                        status: "success",
                        jobState,
                        jobAddress: jobOrDeploymentId,
                        deploymentId: null,
                        deploymentStatus: null,
                        runAddress: jobDetails.project,
                        nodeAddress: jobDetails.node,
                        price: jobDetails.price.toString(),
                        ipfsResult: jobDetails.ipfsResult,
                        serviceUrl: null,
                        endpoints: [],
                    };
                }

                throw new Error(`Could not find deployment or job: ${jobOrDeploymentId}`);
            }

            // Wallet mode: use on-chain SDK
            const addr = address(jobOrDeploymentId);
            const job = await retry(() => this.client.jobs.get(addr));
            const isRunning = job.state === JobState.RUNNING;
            let serviceUrl: string | null = null;

            // Check cached service URL
            for (const [, depInfo] of this.watchedDeployments) {
                if (depInfo.jobAddresses.includes(jobOrDeploymentId) && depInfo.serviceUrl) {
                    serviceUrl = depInfo.serviceUrl;
                    break;
                }
            }

            if (isRunning && !serviceUrl && job.ipfsJob) {
                try {
                    const rawDef = await retry(() => this.client.ipfs.retrieve(job.ipfsJob!));
                    if (rawDef) {
                        const jobDefinition = rawDef as JobDefinition;
                        const services = getJobExposedServices(jobDefinition, jobOrDeploymentId);
                        if (services && services.length > 0) {
                            const domain = process.env.NOSANA_INGRESS_DOMAIN || "node.k8s.prd.nos.ci";
                            serviceUrl = `https://${services[0].hash}.${domain}`;
                        }
                    }
                } catch (e) {
                    console.error("Failed to resolve service URL:", e);
                }
            }

            return {
                status: "success",
                jobState: job.state,
                jobAddress: jobOrDeploymentId,
                deploymentId: null,
                deploymentStatus: null,
                runAddress: job.project,
                nodeAddress: job.node,
                price: job.price.toString(),
                ipfsResult: job.ipfsResult,
                serviceUrl: serviceUrl,
                endpoints: [],
            };
        } catch (error: any) {
            throw new Error(`Get Job Error: ${error.message}`);
        }
    }

    async getJobLogs(jobAddress: string) {
        try {
            if (this.authMode === 'api') {
                // Try to find deployment for this job
                const depInfo = this.findDeploymentByJobAddress(jobAddress);

                if (depInfo) {
                    try {
                        const jobResult = await this.getDeploymentJob(depInfo.deploymentId, jobAddress);
                        if (jobResult.jobResult) {
                            return { status: "completed", result: jobResult.jobResult };
                        }
                        return { status: "pending", logs: ["Job is running or hasn't posted results yet."] };
                    } catch (e) {
                        // Fall through to legacy
                    }
                }

                // Try retrieving via the legacy job definition endpoint
                try {
                    const resultData = await this.apiRequest<any>(
                        `/deployments/jobs/${jobAddress}/results`,
                        { method: 'GET' }
                    );
                    if (resultData) {
                        return { status: "completed", isConfidential: true, result: resultData };
                    }
                } catch (e) {
                    // Not available via deployment jobs endpoint
                }
            }

            // Wallet mode or fallback
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
            if (this.authMode === 'api') {
                console.log(`[Confidential] Fetching results via Deployments API for ${jobAddress}...`);

                // Try deployments/jobs/{job}/results endpoint
                try {
                    const resultData = await this.apiRequest<any>(
                        `/deployments/jobs/${jobAddress}/results`,
                        { method: 'GET' }
                    );
                    if (resultData) {
                        return { status: "completed", isConfidential: true, result: resultData };
                    }
                } catch (e) {
                    // Fall through
                }

                return { status: "pending", logs: ["Results not yet available"] };
            } else {
                // Wallet mode - use the old approach
                const addr = address(jobAddress);
                const job = await this.client.jobs.get(addr);

                if (!job.ipfsJob) return { status: "pending", logs: ["Job has no IPFS hash."] };

                const headers = await this.client.authorization.generateHeaders(job.ipfsJob, { includeTime: true } as any);
                let fetchHeaders: any = {};
                headers.forEach((value, key) => { fetchHeaders[key] = value; });

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
            }
        } catch (e: any) {
            console.error(`[Confidential] Failed to retrieve results:`, e);
            return { status: "error", logs: [`Failed to retrieve confidential results: ${e.message}`] };
        }
    }

    async getBalance() {
        if (this.authMode === 'api') {
            try {
                const balance = await this.apiRequest<{
                    assignedCredits: number;
                    reservedCredits: number;
                    settledCredits: number;
                }>('/credits/balance');

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
                try {
                    const balance = await this.client.api.credits.balance();
                    return {
                        sol: 0,
                        nos: (balance as any).amount || "0",
                        address: "API_ACCOUNT"
                    };
                } catch (e) {
                    throw error;
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
            console.log("[Recovery] Attempting to recover deployments for API mode...");
            try {
                // Use the deployments list endpoint to find running deployments
                try {
                    const deploymentsResult = await this.apiRequest<{
                        deployments: DeploymentResponse[];
                        pagination: any;
                    }>('/deployments?status=RUNNING,STARTING&limit=100');

                    for (const dep of deploymentsResult.deployments) {
                        if (!this.watchedDeployments.has(dep.id)) {
                            console.log(`[Recovery] Found running deployment: ${dep.id} (status: ${dep.status})`);

                            // Get job addresses
                            const jobAddresses: string[] = [];
                            try {
                                const jobsResult = await this.getDeploymentJobs(dep.id, 'RUNNING');
                                for (const job of jobsResult.jobs) {
                                    jobAddresses.push(job.job);
                                }
                            } catch (e) {
                                console.warn(`[Recovery] Could not fetch jobs for deployment ${dep.id}`);
                            }

                            // Recover the watchdog
                            this.watchDeployment(dep.id, process.env.ORCHESTRATOR_URL || "http://localhost:8080", {
                                jobAddresses,
                                isConfidential: dep.confidential,
                                marketAddress: dep.market,
                                strategy: dep.strategy,
                                resources_allocated: { gpu_allocated: 1, vcpu_allocated: 8, ram_gb_allocated: 32 },
                            });
                        }
                    }
                } catch (e: any) {
                    console.warn(`[Recovery] Could not list deployments: ${e.message}`);
                }

                // Also check any cached watched deployments
                for (const [depId, depInfo] of this.watchedDeployments.entries()) {
                    try {
                        const deployment = await this.getDeployment(depId);
                        if (deployment.status === 'RUNNING' || deployment.status === 'STARTING') {
                            console.log(`[Recovery] Deployment ${depId} is still running`);
                        } else {
                            console.log(`[Recovery] Deployment ${depId} is no longer running (status: ${deployment.status})`);
                            this.watchedDeployments.delete(depId);
                        }
                    } catch (e: any) {
                        console.warn(`[Recovery] Could not check deployment ${depId}:`, e.message || e);
                    }
                }

                console.log("[Recovery] API mode recovery complete");
            } catch (e: any) {
                console.error("[Recovery] Failed to recover deployments:", e);
            }
            return;
        }

        // Wallet mode
        if (!this.client.wallet) return;
        try {
            const jobs = await retry(() => this.client.jobs.all());
            const myAddress = this.client.wallet.address.toString();
            const myJobs = jobs.filter((j: any) => j.project?.toString() === myAddress);

            for (const job of myJobs) {
                const jobAddress = job.address.toString();
                const state = job.state;
                if (((state as any) === JobState.RUNNING || (state as any) === 1) && !this.isJobWatched(jobAddress)) {
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

    /**
     * Watch a deployment for status changes, send heartbeats, etc.
     * This is the deployment-centric version of watchJob.
     */
    async watchDeployment(
        deploymentId: string,
        orchestratorUrl: string,
        options?: {
            jobDefinition?: any;
            marketAddress?: string;
            jobAddresses?: string[];
            isConfidential?: boolean;
            strategy?: DeploymentStrategy;
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

        const depInfo: WatchedDeploymentInfo = {
            deploymentId,
            jobAddresses: options?.jobAddresses || [],
            startTime: now,
            jobDefinition: options?.jobDefinition || null,
            marketAddress: options?.marketAddress || "",
            isConfidential: options?.isConfidential !== undefined ? options.isConfidential : true,
            strategy: options?.strategy || 'SIMPLE-EXTEND',
            resources,
            userStopped: false,
            credentialName: options?.credentialName,
        };
        this.watchedDeployments.set(deploymentId, depInfo);

        let lastStatus: DeploymentStatus | null = null;
        let lastHeartbeat = 0;

        console.log(`[watchdog] Started watching deployment: ${deploymentId}`);

        this.sendAuditLog({
            action: "WATCHDOG_STARTED",
            jobAddress: deploymentId,
            details: { resources, strategy: depInfo.strategy }
        });

        while (true) {
            try {
                const currentTime = Date.now();
                const currentDepInfo = this.watchedDeployments.get(deploymentId);

                if (!currentDepInfo) {
                    console.log(`[watchdog] Deployment ${deploymentId} removed from watch list, stopping loop`);
                    return;
                }

                // Get deployment status
                let deployment: DeploymentResponse;
                try {
                    deployment = await this.getDeployment(deploymentId);
                } catch (e: any) {
                    console.error(`[watchdog] Failed to get deployment ${deploymentId}: ${e.message}`);
                    await new Promise((r) => setTimeout(r, 60000));
                    continue;
                }

                if (deployment.status !== lastStatus) {
                    console.log(`[watchdog] Deployment status changed: ${lastStatus} -> ${deployment.status} for ${deploymentId}`);
                    this.sendAuditLog({
                        action: "DEPLOYMENT_STATUS_CHANGED",
                        jobAddress: deploymentId,
                        details: { old_status: lastStatus, new_status: deployment.status }
                    });
                    lastStatus = deployment.status;
                }

                // Update endpoints/service URL from deployment
                if (deployment.endpoints && deployment.endpoints.length > 0) {
                    currentDepInfo.serviceUrl = deployment.endpoints[0].url;
                }

                // Update job addresses
                try {
                    const jobsResult = await this.getDeploymentJobs(deploymentId, 'RUNNING');
                    currentDepInfo.jobAddresses = jobsResult.jobs.map(j => j.job);
                } catch (e) {
                    // Keep existing job addresses
                }

                // If deployment is running, send heartbeats
                if (deployment.status === 'RUNNING') {
                    // Heartbeat using the first job address or deployment ID
                    const instanceId = currentDepInfo.jobAddresses[0] || deploymentId;

                    if (currentTime - lastHeartbeat > 30000) {
                        try {
                            const payload = {
                                provider: "nosana",
                                provider_instance_id: instanceId,
                                deployment_id: deploymentId,
                                gpu_allocated: currentDepInfo.resources.gpu_allocated,
                                vcpu_allocated: currentDepInfo.resources.vcpu_allocated,
                                ram_gb_allocated: currentDepInfo.resources.ram_gb_allocated,
                                health_score: 100,
                                state: "ready",
                                expose_url: currentDepInfo.serviceUrl,
                            };
                            await fetch(`${orchestratorUrl}/inventory/heartbeat`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(payload),
                            });
                            lastHeartbeat = currentTime;
                        } catch (err) {
                            console.error(`[heartbeat] Failed to send heartbeat for ${deploymentId}:`, err);
                        }
                    }
                }

                // Termination check
                const isTerminated =
                    deployment.status === 'STOPPED' ||
                    deployment.status === 'ERROR' ||
                    deployment.status === 'ARCHIVED' ||
                    deployment.status === 'INSUFFICIENT_FUNDS';

                if (isTerminated) {
                    const runtime = currentTime - currentDepInfo.startTime;
                    const runtimeMins = Math.round(runtime / 60000);
                    console.log(`[watchdog] Deployment ${deploymentId} ended (status: ${deployment.status}) after ${runtimeMins} min`);

                    this.sendAuditLog({
                        action: "WATCHDOG_TERMINATED",
                        jobAddress: deploymentId,
                        details: {
                            final_status: deployment.status,
                            runtime_mins: runtimeMins,
                            user_stopped: currentDepInfo.userStopped
                        }
                    });

                    const shouldRedeploy =
                        !currentDepInfo.userStopped &&
                        currentDepInfo.jobDefinition &&
                        currentDepInfo.marketAddress &&
                        runtime >= MIN_RUNTIME_FOR_REDEPLOY_MS;

                    const tooShort = runtime < MIN_RUNTIME_FOR_REDEPLOY_MS;
                    const instanceId = currentDepInfo.jobAddresses[0] || deploymentId;

                    if (currentDepInfo.userStopped) {
                        // User requested stop, don't redeploy
                    } else if (tooShort) {
                        try {
                            const payload = {
                                provider: "nosana",
                                provider_instance_id: instanceId,
                                deployment_id: deploymentId,
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
                        console.log(`[auto-redeploy] Attempting redeploy for ${deploymentId}...`);
                        try {
                            const newResult = await this.launchJob(
                                currentDepInfo.jobDefinition,
                                currentDepInfo.marketAddress,
                                currentDepInfo.isConfidential
                            );

                            const newDeploymentId = newResult.deploymentId || newResult.jobAddress;

                            try {
                                const updatePayload = {
                                    provider: "nosana",
                                    provider_instance_id: newResult.jobAddress,
                                    deployment_id: newDeploymentId,
                                    old_provider_instance_id: instanceId,
                                    gpu_allocated: currentDepInfo.resources.gpu_allocated,
                                    vcpu_allocated: currentDepInfo.resources.vcpu_allocated,
                                    ram_gb_allocated: currentDepInfo.resources.ram_gb_allocated,
                                    health_score: 50,
                                    state: "provisioning",
                                };
                                await fetch(`${orchestratorUrl}/inventory/heartbeat`, {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify(updatePayload),
                                });
                            } catch (err) { }

                            this.watchDeployment(newDeploymentId, orchestratorUrl, {
                                jobDefinition: currentDepInfo.jobDefinition,
                                marketAddress: currentDepInfo.marketAddress,
                                isConfidential: currentDepInfo.isConfidential,
                                strategy: currentDepInfo.strategy,
                                resources_allocated: currentDepInfo.resources,
                                credentialName: currentDepInfo.credentialName,
                            });
                        } catch (redeployErr: any) {
                            console.error(`[auto-redeploy] Failed:`, redeployErr);
                            try {
                                const payload = {
                                    provider: "nosana",
                                    provider_instance_id: instanceId,
                                    deployment_id: deploymentId,
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

                    // Send terminated heartbeat
                    try {
                        const payload = {
                            provider: "nosana",
                            provider_instance_id: instanceId,
                            deployment_id: deploymentId,
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

                    this.watchedDeployments.delete(deploymentId);
                    return;
                }

            } catch (error) {
                console.error(`[watchdog] Error loop ${deploymentId}:`, error);
            }

            await new Promise((r) => setTimeout(r, 60000));
        }
    }

    /**
     * Legacy watchJob method — now delegates to watchDeployment for API mode.
     * Kept for backward compatibility with server.ts.
     */
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
        if (this.authMode === 'api' && options?.deploymentUuid) {
            // Delegate to deployment-level watchdog
            return this.watchDeployment(options.deploymentUuid, orchestratorUrl, {
                jobDefinition: options?.jobDefinition,
                marketAddress: options?.marketAddress,
                jobAddresses: [jobAddress],
                isConfidential: options?.isConfidential,
                strategy: 'SIMPLE-EXTEND',
                resources_allocated: options?.resources_allocated,
                credentialName: options?.credentialName,
            });
        }

        // Wallet mode: use the on-chain watchdog
        const now = Date.now();
        const resources = options?.resources_allocated || {
            gpu_allocated: 1,
            vcpu_allocated: 8,
            ram_gb_allocated: 32
        };

        const depInfo: WatchedDeploymentInfo = {
            deploymentId: options?.deploymentUuid || jobAddress,
            jobAddresses: [jobAddress],
            startTime: now,
            jobDefinition: options?.jobDefinition || null,
            marketAddress: options?.marketAddress || "",
            isConfidential: options?.isConfidential !== undefined ? options.isConfidential : true,
            strategy: 'SIMPLE-EXTEND',
            resources,
            userStopped: false,
            credentialName: options?.credentialName,
        };
        this.watchedDeployments.set(depInfo.deploymentId, depInfo);

        let lastState: JobState | null = null;
        let lastHeartbeat = 0;

        console.log(`[watchdog] Started watching job: ${jobAddress}`);

        while (true) {
            try {
                const currentTime = Date.now();
                const job = await this.getJob(jobAddress);
                const currentInfo = this.watchedDeployments.get(depInfo.deploymentId);

                if (!currentInfo) {
                    console.log(`[watchdog] Job ${jobAddress} removed from watch list, stopping loop`);
                    return;
                }

                if (job.jobState !== lastState) {
                    console.log(`[watchdog] Job state changed: ${lastState} -> ${job.jobState} for ${jobAddress}`);
                    lastState = job.jobState;
                }

                // Heartbeat for running jobs
                if ((job.jobState as any) === JobState.RUNNING || (job.jobState as any) === 1) {
                    if (currentTime - lastHeartbeat > 30000) {
                        try {
                            const payload = {
                                provider: "nosana",
                                provider_instance_id: jobAddress,
                                gpu_allocated: currentInfo.resources.gpu_allocated,
                                vcpu_allocated: currentInfo.resources.vcpu_allocated,
                                ram_gb_allocated: currentInfo.resources.ram_gb_allocated,
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
                            console.error(`[heartbeat] Failed for ${jobAddress}:`, err);
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
                    const runtime = currentTime - currentInfo.startTime;
                    const runtimeMins = Math.round(runtime / 60000);
                    console.log(`[watchdog] Job ${jobAddress} ended (state: ${job.jobState}) after ${runtimeMins} min`);

                    this.sendAuditLog({
                        action: "WATCHDOG_TERMINATED",
                        jobAddress,
                        details: {
                            final_state: state,
                            runtime_mins: runtimeMins,
                            user_stopped: currentInfo.userStopped
                        }
                    });

                    const shouldRedeploy =
                        !currentInfo.userStopped &&
                        currentInfo.jobDefinition &&
                        currentInfo.marketAddress &&
                        runtime >= MIN_RUNTIME_FOR_REDEPLOY_MS;

                    const tooShort = runtime < MIN_RUNTIME_FOR_REDEPLOY_MS;

                    if (currentInfo.userStopped) {
                        // User stopped, do nothing
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
                        } catch (err: any) {
                            console.error(`[heartbeat] Failed to send failed heartbeat for short-lived job ${jobAddress}:`, err.message || err);
                        }
                    } else if (shouldRedeploy) {
                        console.log(`[auto-redeploy] Attempting redeploy for ${jobAddress}...`);
                        try {
                            const newJob = await this.launchJob(
                                currentInfo.jobDefinition,
                                currentInfo.marketAddress,
                                currentInfo.isConfidential
                            );

                            try {
                                const updatePayload = {
                                    provider: "nosana",
                                    provider_instance_id: newJob.jobAddress,
                                    old_provider_instance_id: jobAddress,
                                    gpu_allocated: currentInfo.resources.gpu_allocated,
                                    vcpu_allocated: currentInfo.resources.vcpu_allocated,
                                    ram_gb_allocated: currentInfo.resources.ram_gb_allocated,
                                    health_score: 50,
                                    state: "provisioning",
                                };
                                await fetch(`${orchestratorUrl}/inventory/heartbeat`, {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify(updatePayload),
                                });
                            } catch (err: any) {
                                console.error(`[heartbeat] Failed to send provisioning heartbeat for redeployed job ${newJob.jobAddress}:`, err.message || err);
                            }

                            this.watchJob(newJob.jobAddress, orchestratorUrl, {
                                jobDefinition: currentInfo.jobDefinition,
                                marketAddress: currentInfo.marketAddress,
                                isConfidential: currentInfo.isConfidential,
                                deploymentUuid: newJob.deploymentUuid,
                                resources_allocated: currentInfo.resources,
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
                            } catch (err: any) {
                                console.error(`[heartbeat] Failed to send failed heartbeat after redeploy error for ${jobAddress}:`, err.message || err);
                            }
                        }
                    }

                    // Send terminated heartbeat
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
                    } catch (err: any) {
                        console.error(`[heartbeat] Failed to send terminated heartbeat for ${jobAddress}:`, err.message || err);
                    }

                    this.watchedDeployments.delete(depInfo.deploymentId);
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
        const total = this.watchedDeployments.size;
        if (total > 0) {
            console.log(`[watchdog-summary] Currently watching ${total} deployments.`);
        }
    }

    /**
     * Find a deployment by one of its job addresses
     */
    private findDeploymentByJobAddress(jobAddress: string): WatchedDeploymentInfo | null {
        // Direct deployment ID match
        if (this.watchedDeployments.has(jobAddress)) {
            return this.watchedDeployments.get(jobAddress)!;
        }
        // Search by job address within deployments
        for (const [, info] of this.watchedDeployments.entries()) {
            if (info.jobAddresses.includes(jobAddress)) {
                return info;
            }
        }
        return null;
    }

    /**
     * Check if a job is already watched (by any deployment)
     */
    private isJobWatched(jobAddress: string): boolean {
        for (const [, info] of this.watchedDeployments.entries()) {
            if (info.jobAddresses.includes(jobAddress)) {
                return true;
            }
        }
        return false;
    }

    /**
     * Get all watched deployment IDs that use a specific credential.
     */
    getWatchedJobsByCredential(credentialName: string): string[] {
        const ids: string[] = [];
        for (const [depId, depInfo] of this.watchedDeployments.entries()) {
            if (depInfo.credentialName === credentialName) {
                ids.push(depId);
            }
        }
        return ids;
    }

    /**
     * Get all watched deployment IDs.
     */
    getAllWatchedJobs(): string[] {
        return Array.from(this.watchedDeployments.keys());
    }

    /**
     * Mark a deployment as user-stopped to prevent auto-redeploy.
     */
    markJobForNoRedeploy(jobOrDeploymentId: string): void {
        const depInfo = this.watchedDeployments.get(jobOrDeploymentId) || this.findDeploymentByJobAddress(jobOrDeploymentId);
        if (depInfo) {
            depInfo.userStopped = true;
            console.log(`[watchdog] Deployment ${depInfo.deploymentId} marked to skip auto-redeploy`);
        }
    }
}
