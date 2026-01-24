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
    startTime: number;
    lastExtendTime: number;
    jobDefinition: any;
    marketAddress: string;
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
        if (retries > 0 && (error.message.includes("429") || error.message.includes("Too Many Requests"))) {
            console.log(`[retry] Got 429, retrying in ${delay}ms... (${retries} left)`);
            await new Promise(resolve => setTimeout(resolve, delay));
            return retry(fn, retries - 1, delay * 2);
        }
        throw error;
    }
}

export class NosanaService {
    private client: NosanaClient;
    private privateKey: string;
    private watchedJobs = new Map<string, WatchedJobInfo>();
    private summaryInterval: number = 60000;

    constructor(privateKey: string, rpcUrl?: string) {
        this.privateKey = privateKey;
        this.client = createNosanaClient(NosanaNetwork.MAINNET, {
            solana: {
                rpcEndpoint: rpcUrl || "https://api.mainnet-beta.solana.com",
            },
        });
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
        if (this.privateKey) {
            try {
                const secretKey = bs58.decode(this.privateKey);
                const signer = await createKeyPairSignerFromBytes(secretKey);
                this.client.wallet = signer;
                const walletAddr = this.client.wallet ? this.client.wallet.address : "Unknown";
                console.log(`Nosana Adapter initialized. Wallet: ${walletAddr}`);
            } catch (e) {
                console.error("Failed to initialize Nosana wallet:", e);
                throw e;
            }
        }
    }

    async launchJob(jobDefinition: any, marketAddress: string) {
        try {
            console.log("Pinning job to IPFS...");
            const ipfsHash = await this.client.ipfs.pin(jobDefinition);
            console.log(`IPFS Hash: ${ipfsHash}`);

            console.log(`Listing on market: ${marketAddress}`);
            const instruction = await this.client.jobs.post({
                ipfsHash,
                market: address(marketAddress),
                timeout: 1800,
            });

            let jobAddress = "unknown";
            if (instruction.accounts && instruction.accounts.length > 0) {
                jobAddress = instruction.accounts[0].address;
            }

            const signature = await this.client.solana.buildSignAndSend(instruction);

            this.sendAuditLog({
                action: "JOB_LAUNCHED",
                jobAddress,
                details: { ipfsHash, marketAddress, signature }
            });

            return {
                status: "success",
                jobAddress: jobAddress,
                ipfsHash: ipfsHash,
                txSignature: signature,
            };
        } catch (error: any) {
            console.error("Launch Error:", error);
            throw new Error(`Nosana SDK Error: ${error.message}`);
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
            // Using internal endpoint which might require a mock internal key or similar if enforced
            // Assuming this runs in a trusted environment or we need to add a header.
            // For now, simple fetch.
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
            console.log(`Attempting to stop job: ${jobAddress}`);
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
        if (!this.client.wallet) throw new Error("Wallet not initialized");
        return new LogStreamer(this.client.wallet);
    }

    async getJob(jobAddress: string) {
        // ... (existing implementation) ...
        try {
            const addr = address(jobAddress);
            const job = await retry(() => this.client.jobs.get(addr));
            const isRunning = job.state === JobState.RUNNING;
            let serviceUrl: string | null = null;

            // OPTIMIZATION: Check cache first
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

                            // Update cache
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
        // ... (existing implementation) ...
        try {
            const addr = address(jobAddress);
            const job = await retry(() => this.client.jobs.get(addr));

            if (!job.ipfsResult) {
                return { status: "pending", logs: ["Job is running or hasn't posted results yet."] };
            }

            const result = await retry(() => this.client.ipfs.retrieve(job.ipfsResult!));
            return { status: "completed", ipfsHash: job.ipfsResult, result: result };
        } catch (error: any) {
            console.error("Get Logs Error:", error);
            throw new Error(`Get Logs Error: ${error.message}`);
        }
    }

    async getBalance() {
        // ... (existing implementation) ...
        const sol = await this.client.solana.getBalance();
        const nos = await this.client.nos.getBalance();
        return {
            sol: sol,
            nos: nos.toString() || "0",
            address: this.client.wallet ? this.client.wallet.address : "Unknown",
        };
    }

    async recoverJobs() {
        // ... (existing implementation) ...
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
            startTime: now,
            lastExtendTime: now,
            jobDefinition: options?.jobDefinition || null,
            marketAddress: options?.marketAddress || "",
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
            details: { resources }
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
                        // ... (heartbeat logic) ...
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

                    // Auto-Redeploy Logic
                    const shouldRedeploy =
                        !currentJobInfo.userStopped &&
                        currentJobInfo.jobDefinition &&
                        currentJobInfo.marketAddress &&
                        runtime >= MIN_RUNTIME_FOR_REDEPLOY_MS;

                    const tooShort = runtime < MIN_RUNTIME_FOR_REDEPLOY_MS;

                    if (currentJobInfo.userStopped) {
                        // User stopped
                    } else if (tooShort) {
                        // Too short, failed
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

                        this.sendAuditLog({
                            action: "JOB_AUTO_REDEPLOY_ATTEMPT",
                            jobAddress,
                            details: { runtime_mins: runtimeMins }
                        });

                        try {
                            const newJob = await this.launchJob(
                                currentJobInfo.jobDefinition,
                                currentJobInfo.marketAddress
                            );

                            this.sendAuditLog({
                                action: "JOB_AUTO_REDEPLOY_SUCCESS",
                                jobAddress: newJob.jobAddress, // New ID
                                details: { old_job_address: jobAddress }
                            });

                            // Notify orchestrator...
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
                                resources_allocated: currentJobInfo.resources,
                            });
                        } catch (redeployErr: any) {
                            console.error(`[auto-redeploy] Failed:`, redeployErr);

                            this.sendAuditLog({
                                action: "JOB_AUTO_REDEPLOY_FAILED",
                                jobAddress,
                                status: "error",
                                details: { error: redeployErr.message }
                            });

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
                    } else {
                        // No redeploy
                    }

                    // Final terminated heartbeat
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
