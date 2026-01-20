import {
    createNosanaClient,
    NosanaClient,
    NosanaNetwork,
    getJobExposedServices,
    JobState,
} from "@nosana/kit";

import { address, createKeyPairSignerFromBytes } from "@solana/kit";
import bs58 from "bs58";
import type { JobDefinition } from "@nosana/types";
import fetch from "node-fetch";

export class NosanaService {
    private client: NosanaClient;
    private watchedJobs = new Set<string>();
    private ingressDomain: string;
    private webhookUrl: string;
    private watchInterval: number;

    constructor(privateKey: string, rpcUrl?: string) {
        this.ingressDomain =
            process.env.NOSANA_INGRESS_DOMAIN || "node.k8s.prd.nos.ci";

        this.webhookUrl = process.env.NOSANA_EVENT_WEBHOOK || "";

        this.watchInterval = Number(
            process.env.NOSANA_WATCH_INTERVAL_MS || 3000
        );

        this.client = createNosanaClient(NosanaNetwork.MAINNET, {
            solana: {
                rpcEndpoint: rpcUrl || "https://api.mainnet-beta.solana.com",
            },
        });

        if (!privateKey) {
            throw new Error("NOSANA private key missing");
        }

        this.initWallet(privateKey);
    }

    private async initWallet(privateKey: string) {
        const secretKey = bs58.decode(privateKey);
        const signer = await createKeyPairSignerFromBytes(secretKey);
        this.client.wallet = signer;
        console.log(`Nosana wallet initialized: ${signer.address}`);
    }

    // --------------------------------------------------
    // LAUNCH JOB (NON-BLOCKING)
    // --------------------------------------------------
    async launchJob(jobDefinition: JobDefinition, marketAddress: string) {
        const ipfsHash = await this.client.ipfs.pin(jobDefinition);

        const instruction = await this.client.jobs.post({
            ipfsHash,
            market: address(marketAddress),
            timeout: 1800,
        });

        const jobAddress = instruction.accounts?.[0]?.address;
        if (!jobAddress) {
            throw new Error("Failed to derive job address");
        }

        // 🔥 Fire-and-forget transaction
        this.client.solana
            .buildSignAndSend(instruction)
            .then((sig) => console.log(`Nosana tx confirmed: ${sig}`))
            .catch((err) => console.error("Solana tx failed:", err));

        // 🔥 Start watcher immediately
        this.watchJob(jobAddress);

        return {
            status: "submitted",
            jobAddress,
            ipfsHash,
        };
    }

    // --------------------------------------------------
    // JOB WATCHER (PUSH-BASED DISCOVERY)
    // --------------------------------------------------
    private async watchJob(jobAddress: string) {
        if (this.watchedJobs.has(jobAddress)) return;
        this.watchedJobs.add(jobAddress);

        const addr = address(jobAddress);

        const loop = async () => {
            try {
                const job = await this.client.jobs.get(addr);

                if (job.state === JobState.RUNNING && job.ipfsJob) {
                    const rawDef = await this.client.ipfs.retrieve(job.ipfsJob);

                    const jobDef = rawDef as JobDefinition;
                    const services = getJobExposedServices(jobDef, jobAddress);

                    if (services?.length) {
                        const serviceUrl = `https://${services[0].hash}.${this.ingressDomain}`;

                        await this.emitRunningEvent(jobAddress, serviceUrl);
                        this.watchedJobs.delete(jobAddress);
                        return;
                    }
                }

                if (
                    job.state === JobState.CANCELLED ||
                    job.state === JobState.FINISHED
                ) {
                    this.watchedJobs.delete(jobAddress);
                    return;
                }
            } catch (e) {
                console.error(`Watcher error for ${jobAddress}`, e);
            }

            setTimeout(loop, this.watchInterval);
        };

        loop();
    }

    // --------------------------------------------------
    // PUSH EVENT TO ORCHESTRATOR
    // --------------------------------------------------
    private async emitRunningEvent(jobAddress: string, serviceUrl: string) {
        if (!this.webhookUrl) {
            console.warn("Webhook URL not configured");
            return;
        }

        await fetch(this.webhookUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                event: "NOSANA_JOB_RUNNING",
                jobAddress,
                serviceUrl,
            }),
        });

        console.log(`Emitted RUNNING event for ${jobAddress}`);
    }

    // --------------------------------------------------
    // STOP JOB (IDEMPOTENT)
    // --------------------------------------------------
    async stopJob(jobAddress: string) {
        const addr = address(jobAddress);
        const job = await this.client.jobs.get(addr);

        let instruction;

        if (job.state === JobState.RUNNING) {
            instruction = await this.client.jobs.end({ job: addr });
        } else if (job.state === JobState.QUEUED) {
            instruction = await this.client.jobs.delist({ job: addr });
        } else {
            return { status: "noop", state: job.state };
        }

        const sig = await this.client.solana.buildSignAndSend(instruction);

        return { status: "stopped", txSignature: sig };
    }
}
