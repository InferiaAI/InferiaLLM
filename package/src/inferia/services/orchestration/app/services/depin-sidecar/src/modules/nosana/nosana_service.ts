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
                console.log(`[Launch] Creating deployment via API in market: ${marketAddress}`);
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
                        ipfsHash = jobs[0].ipfs_job;
                        console.log(`[Launch] Resolved Job Address from API: ${jobAddress}`);
                        break;
                    }
                    await new Promise(r => setTimeout(r, 2000));
                    attempts++;
                }

                if (jobAddress === "unknown") {
                    throw new Error("Timeout waiting for Job Address from Nosana API");
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

            if (this.authMode === 'api' && deploymentUuid) {
                console.log(`[Confidential] Requesting Auth Header from API for deployment ${deploymentUuid}...`);
                const deployment = await this.client.api.deployments.get(deploymentUuid);
                const authHeader = await (deployment as any).generateAuthHeader();
                fetchHeaders['Authorization'] = authHeader;
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
                     
                     // Regenerate headers
                     if (this.authMode === 'api' && deploymentUuid) {
                         const deployment = await this.client.api.deployments.get(deploymentUuid);
                         fetchHeaders['Authorization'] = await (deployment as any).generateAuthHeader();
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
        const filtrationUrl = process.env.FILTRATION_URL || "http://localhost:8000";
        const payload = {
            action: event.action,
            resource_type: "job",
            resource_id: event.jobAddress,
            details: event.details || {},
            status: event.status || "success",
        };

        try {
            await fetch(`${filtrationUrl}/audit/internal/log`, {
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
                await this.client.api.jobs.stop(jobAddress);
                console.log(`Job ${jobAddress} stopped via API`);
                return { status: "stopped" };
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
                await this.client.api.jobs.extend({ address: jobAddress, seconds: duration } as any);
                return { status: "success", jobAddress };
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
        if (!this.client.wallet && this.authMode === 'wallet') throw new Error("Wallet not initialized");
        return new LogStreamer(this.client.wallet as any);
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
            
            const cachedJob = this.watchedJobs.get(jobAddress);
            if (this.authMode === 'api' && cachedJob?.deploymentUuid) {
                const deployment = await this.client.api.deployments.get(cachedJob.deploymentUuid);
                fetchHeaders['Authorization'] = await (deployment as any).generateAuthHeader();
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
            const balance = await this.client.api.credits.balance();
            return {
                sol: 0,
                nos: (balance as any).amount || "0",
                address: "API_ACCOUNT"
            };
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
            console.log("Job recovery for API mode is handled by Deployment listing (not implemented yet)");
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
}
