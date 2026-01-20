import { createNosanaClient, NosanaClient, NosanaNetwork, getJobExposedServices, JobState } from '@nosana/kit';
import { address, createKeyPairSignerFromBytes } from '@solana/kit';
import bs58 from 'bs58';
import type { JobDefinition } from '@nosana/types';

// Job timing constants (in milliseconds)
const JOB_TIMEOUT_MS = 30 * 60 * 1000;        // 30 minutes default timeout
const EXTEND_THRESHOLD_MS = 5 * 60 * 1000;    // Extend 5 mins before timeout
const EXTEND_DURATION_SECS = 1800;            // Extend by 30 minutes (in seconds)
const MIN_RUNTIME_FOR_REDEPLOY_MS = 20 * 60 * 1000;  // 20 minutes minimum before redeploy

// Tracked job metadata for auto-extend and auto-redeploy
interface WatchedJobInfo {
    jobAddress: string;
    startTime: number;              // When job was launched (epoch ms)
    lastExtendTime: number;         // When job was last extended (epoch ms)
    jobDefinition: any;             // Original job definition for re-deploy
    marketAddress: string;          // Market for re-deploy
    resources: {
        gpu_allocated: number;
        vcpu_allocated: number;
        ram_gb_allocated: number;
    };
    userStopped: boolean;           // Flag to prevent re-deploy on user stop
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

        // Initialize client without wallet first
        // We pass a partial config. ClientConfig requires 'solana'.
        this.client = createNosanaClient(NosanaNetwork.MAINNET, {
            solana: {
                rpcEndpoint: rpcUrl || "https://api.mainnet-beta.solana.com",
            },
        });

        this.startWatchdogSummary();
    }

    /**
     * Mark a job as stopping (user-initiated).
     * This prevents auto-redeploy when the job terminates.
     */
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
                // Decode private key (base58) to bytes
                const secretKey = bs58.decode(this.privateKey);
                // Create signer
                const signer = await createKeyPairSignerFromBytes(secretKey);
                // Assign to client
                this.client.wallet = signer;

                // Safe access to address
                const walletAddr = this.client.wallet
                    ? this.client.wallet.address
                    : "Unknown";
                console.log(
                    `Nosana Adapter initialized. Wallet: ${walletAddr}`
                );
            } catch (e) {
                console.error("Failed to initialize wallet:", e);
                throw e;
            }
        }
    }

    /**
     * Launches a job in 2 steps:
     * 1. Uploads the JSON definition to Nosana's IPFS Gateway.
     * 2. Posts the job offer to the Solana Blockchain.
     */
    async launchJob(jobDefinition: any, marketAddress: string) {
        try {
            // Check and log balance before launch
            try {
                const balance = await this.getBalance();
                // @ts-ignore
                console.log(`[Launch] Wallet Balance: ${parseInt(balance.sol.toString()) / 1e9} SOL, ${balance.nos} NOS`);
            } catch (e) {
                console.warn("[Launch] Failed to check balance:", e);
            }

            // Step A: Upload to IPFS
            console.log("Pinning job to IPFS...");
            const ipfsHash = await this.client.ipfs.pin(jobDefinition);
            console.log(`IPFS Hash: ${ipfsHash}`);

            // Step B: List the job on the Market
            console.log(`Listing on market: ${marketAddress}`);

            const instruction = await this.client.jobs.post({
                ipfsHash,
                market: address(marketAddress),
                timeout: 1800, // Default timeout 30 mins
            });

            // Extract Job Address from the instruction's first account (job signer)
            // The 'post' instruction creates a new keypair for the job and includes it as the first account.
            let jobAddress = "unknown";
            if (instruction.accounts && instruction.accounts.length > 0) {
                jobAddress = instruction.accounts[0].address;
            }
            console.log(`Generated Job Address: ${jobAddress}`);

            const signature = await this.client.solana.buildSignAndSend(
                instruction
            );

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

    /**
     * Stops a job to reclaim rent/credits.
     */
    async stopJob(jobAddress: string) {
        try {
            console.log(`Attempting to stop job: ${jobAddress}`);

            // Cast string to Address
            const addr = address(jobAddress);
            const job = await retry(() => this.client.jobs.get(addr));
            console.log(`Job State: ${job.state}`);

            let instruction;
            if (job.state === JobState.RUNNING) {
                console.log("Job is RUNNING. Calling jobs.end()...");
                instruction = await retry(() => this.client.jobs.end({ job: addr }));
            } else if (job.state === JobState.QUEUED) {
                console.log("Job is QUEUED. Calling jobs.delist()...");
                instruction = await retry(() => this.client.jobs.delist({ job: addr }));
            } else {
                throw new Error(`Cannot stop job in state: ${job.state}`); // Should be safe if state is robust
            }

            const signature = await retry(() => this.client.solana.buildSignAndSend(
                instruction
            ));

            console.log(`Job stopped. Tx: ${signature}`);
            return { status: "stopped", txSignature: signature };
        } catch (error: any) {
            console.error("Stop Job Failed. Full Error:", error);
            const msg =
                error instanceof Error ? error.message : JSON.stringify(error);
            throw new Error(`Stop Error: ${msg}`);
        }
    }

    /**
     * Extends a job's duration.
     */
    async extendJob(jobAddress: string, duration: number) {
        try {
            console.log(
                `Extending job ${jobAddress} by ${duration} seconds...`
            );
            const addr = address(jobAddress);

            const instruction = await this.client.jobs.extend({
                job: addr,
                timeout: duration,
            });

            const signature = await this.client.solana.buildSignAndSend(
                instruction
            );

            return {
                status: "success",
                jobAddress: jobAddress,
                txSignature: signature,
            };
        } catch (error: any) {
            console.error("Extend Error:", error);
            throw new Error(`Nosana SDK Error: ${error.message}`);
        }
    }
    async getJob(jobAddress: string) {
        try {
            const addr = address(jobAddress);
            const job = await retry(() => this.client.jobs.get(addr));

            console.log("FULL JOB OBJECT:", JSON.stringify(job, null, 2));

            const isRunning = job.state === JobState.RUNNING;
            let serviceUrl: string | null = null;

            if (isRunning && job.ipfsJob) {
                try {
                    // Retrieve Job Definition from IPFS
                    const rawDef = await retry(() => this.client.ipfs.retrieve(job.ipfsJob!));
                    if (rawDef) {
                        const jobDefinition = rawDef as JobDefinition;
                        const services = getJobExposedServices(
                            jobDefinition,
                            jobAddress
                        );

                        if (services && services.length > 0) {
                            const domain =
                                process.env.NOSANA_INGRESS_DOMAIN ||
                                "node.k8s.prd.nos.ci";
                            serviceUrl = `https://${services[0].hash}.${domain}`;
                            console.log(`Resolved Service URL: ${serviceUrl}`);
                        } else {
                            console.log(
                                "No exposed services found in job definition."
                            );
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

    /**
     * Retrieves logs/result from IPFS for a completed job.
     */
    async getJobLogs(jobAddress: string) {
        try {
            const addr = address(jobAddress);
            const job = await retry(() => this.client.jobs.get(addr));

            if (!job.ipfsResult) {
                return {
                    status: "pending",
                    logs: ["Job is running or hasn't posted results yet. Logs are available after completion."],
                };
            }

            console.log(`Fetching logs from IPFS: ${job.ipfsResult}`);
            const result = await retry(() => this.client.ipfs.retrieve(job.ipfsResult!));

            // Result is typically { op: "...", status: "...", logs: "..." } or similar
            // We just return the whole result for now, or extract logs if obvious

            return {
                status: "completed",
                ipfsHash: job.ipfsResult,
                result: result,
            };
        } catch (error: any) {
            console.error("Get Logs Error:", error);
            throw new Error(`Get Logs Error: ${error.message}`);
        }
    }

    /**
     * Checks wallet health (SOL for gas, NOS for fees)
     */
    async getBalance() {
        const sol = await this.client.solana.getBalance();
        const nos = await this.client.nos.getBalance();
        return {
            sol: sol,
            nos: nos.toString() || "0",
            address: this.client.wallet
                ? this.client.wallet.address
                : "Unknown",
        };
    }

    /**
     * Recovers running jobs from the blockchain that belong to this wallet.
     */
    async recoverJobs() {
        if (!this.client.wallet) return;
        console.log("Recovering active jobs...");
        try {
            // Retrieve all jobs from the program.
            const jobs = await this.client.jobs.all();

            // Filter for my jobs
            const myAddress = this.client.wallet.address.toString();

            const myJobs = jobs.filter((j: any) => {
                const project = j.project?.toString();
                return project === myAddress;
            });

            console.log(`Found ${myJobs.length} active jobs for wallet ${myAddress}`);

            for (const job of myJobs) {
                const jobAddress = job.address.toString();
                const state = job.state;

                // State 1 = RUNNING
                if (state === JobState.RUNNING || state === 1) {
                    // Check if already watching to avoid duplicates
                    if (!this.watchedJobs.has(jobAddress)) {
                        console.log(`Recovering watchdog for running job: ${jobAddress}`);
                        // For recovered jobs, we don't have original job definition
                        // We use safe defaults and disable auto-redeploy
                        this.watchJob(
                            jobAddress,
                            process.env.ORCHESTRATOR_URL || "http://localhost:8080",
                            {
                                jobDefinition: null,  // Can't redeploy without definition
                                marketAddress: "",
                                resources_allocated: {
                                    gpu_allocated: 1,
                                    vcpu_allocated: 8,
                                    ram_gb_allocated: 32
                                }
                            }
                        );
                    }
                }
            }
        } catch (e: any) {
            console.error("Failed to recover jobs:", e);
        }
    }

    /**
     * Watch a job for state changes, auto-extend before timeout, and auto-redeploy on failure.
     */
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

        // Defaults if recovering or not provided
        const resources = options?.resources_allocated || {
            gpu_allocated: 1,
            vcpu_allocated: 8,
            ram_gb_allocated: 32
        };

        // Register job in watched map with metadata
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
                    lastState = job.jobState;
                }

                // ─────────────────────────────────────────────────────
                // AUTO-EXTEND LOGIC: Extend 5 mins before timeout
                // ─────────────────────────────────────────────────────
                if ((job.jobState as any) === JobState.RUNNING || (job.jobState as any) === 1) {
                    const timeSinceLastExtend = currentTime - currentJobInfo.lastExtendTime;
                    const timeUntilTimeout = JOB_TIMEOUT_MS - timeSinceLastExtend;

                    if (timeUntilTimeout <= EXTEND_THRESHOLD_MS && timeUntilTimeout > 0) {
                        console.log(`[auto-extend] Job ${jobAddress} has ${Math.round(timeUntilTimeout / 1000)}s until timeout, extending by ${EXTEND_DURATION_SECS}s...`);
                        try {
                            await this.extendJob(jobAddress, EXTEND_DURATION_SECS);
                            currentJobInfo.lastExtendTime = currentTime;
                            console.log(`[auto-extend] Successfully extended job ${jobAddress}`);
                        } catch (extendErr) {
                            console.error(`[auto-extend] Failed to extend job ${jobAddress}:`, extendErr);
                        }
                    }

                    // Send heartbeat every 30 seconds
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
                            console.log(`[heartbeat] Sending "ready" to ${orchestratorUrl} for ${jobAddress}`);
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

                // ─────────────────────────────────────────────────────
                // TERMINATION DETECTION
                // ─────────────────────────────────────────────────────
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
                    console.log(`[watchdog] Job ${jobAddress} ended (state: ${job.jobState}) after ${runtimeMins} minutes`);

                    // ─────────────────────────────────────────────────
                    // AUTO-REDEPLOY LOGIC
                    // ─────────────────────────────────────────────────
                    const shouldRedeploy =
                        !currentJobInfo.userStopped &&
                        currentJobInfo.jobDefinition &&
                        currentJobInfo.marketAddress &&
                        runtime >= MIN_RUNTIME_FOR_REDEPLOY_MS;

                    const tooShort = runtime < MIN_RUNTIME_FOR_REDEPLOY_MS;

                    if (currentJobInfo.userStopped) {
                        console.log(`[watchdog] Job ${jobAddress} was stopped by user, not redeploying`);
                    } else if (tooShort) {
                        console.log(`[early-failure] Job ${jobAddress} failed after only ${runtimeMins}min (< 20min), marking as failed, NOT redeploying`);
                        // Send failed heartbeat
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
                        } catch (err) {
                            console.error(`[heartbeat] Failed to send failed state:`, err);
                        }
                    } else if (shouldRedeploy) {
                        console.log(`[auto-redeploy] Job ${jobAddress} ended unexpectedly after ${runtimeMins}min, attempting redeploy...`);
                        try {
                            const newJob = await this.launchJob(
                                currentJobInfo.jobDefinition,
                                currentJobInfo.marketAddress
                            );
                            console.log(`[auto-redeploy] New job launched: ${newJob.jobAddress}`);

                            // Notify orchestrator about the new job address
                            try {
                                const updatePayload = {
                                    provider: "nosana",
                                    provider_instance_id: newJob.jobAddress,
                                    old_provider_instance_id: jobAddress,
                                    gpu_allocated: currentJobInfo.resources.gpu_allocated,
                                    vcpu_allocated: currentJobInfo.resources.vcpu_allocated,
                                    ram_gb_allocated: currentJobInfo.resources.ram_gb_allocated,
                                    health_score: 50,  // Lower score while starting
                                    state: "provisioning",
                                };
                                await fetch(`${orchestratorUrl}/inventory/heartbeat`, {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify(updatePayload),
                                });
                            } catch (err) {
                                console.error(`[auto-redeploy] Failed to notify orchestrator:`, err);
                            }

                            // Start watching the new job
                            this.watchJob(newJob.jobAddress, orchestratorUrl, {
                                jobDefinition: currentJobInfo.jobDefinition,
                                marketAddress: currentJobInfo.marketAddress,
                                resources_allocated: currentJobInfo.resources,
                            });
                        } catch (redeployErr) {
                            console.error(`[auto-redeploy] Failed to redeploy job:`, redeployErr);
                            // Send terminated state on redeploy failure
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
                            } catch (err) {
                                console.error(`[heartbeat] Failed to send failed state:`, err);
                            }
                        }
                    } else {
                        // No redeploy possible (missing job definition), just terminate
                        console.log(`[watchdog] Cannot redeploy job ${jobAddress} (missing job definition or market)`);
                    }

                    // Send final termination heartbeat for the old job
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
                        console.log(`[heartbeat] Successfully reported "terminated" for ${jobAddress}`);
                    } catch (err) {
                        console.error(`[heartbeat] Failed to send final heartbeat for ${jobAddress}:`, err);
                    }

                    this.watchedJobs.delete(jobAddress);
                    return;
                }

            } catch (error) {
                console.error(`[watchdog] Error in loop for job ${jobAddress}:`, error);
            }

            await new Promise((r) => setTimeout(r, 30000)); // 30 seconds to avoid RPC rate limits
        }
    }

    private startWatchdogSummary() {
        setInterval(() => {
            if (this.watchedJobs.size > 0) {
                const jobAddresses = Array.from(this.watchedJobs.keys());
                console.log(`[watchdog-summary] Currently watching ${this.watchedJobs.size} jobs: ${jobAddresses.join(", ")}`);
            } else {
                console.log(`[watchdog-summary] No active jobs being watched.`);
            }
        }, this.summaryInterval);
    }
}
