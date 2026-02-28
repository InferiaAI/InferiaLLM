import express from 'express';
import http from 'http';
import { WebSocketServer, WebSocket } from 'ws';
import dotenv from 'dotenv';
import cors from 'cors';
import axios from 'axios';
import crypto from 'crypto';
import { AkashService } from './modules/akash/akash_service';
import { NosanaService } from './modules/nosana/nosana_service';

dotenv.config();

const app = express();
const server = http.createServer(app);
const wss = new WebSocketServer({ server });
const PORT: number = Number(process.env.PORT) || 3000;

app.use(express.json());
app.use(cors());

// --- Configuration Constants ---
const API_GATEWAY_URL = process.env.API_GATEWAY_URL || "http://localhost:8000";
const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || "dev-internal-key-change-in-prod";

console.log(`[Sidecar] Configured to fetch settings from: ${API_GATEWAY_URL}`);

// --- Initialize Services ---
const akashService = new AkashService();

// Multi-credential support: Map of credential name -> NosanaService
const nosanaServices: Map<string, NosanaService> = new Map();
let defaultNosanaService: NosanaService | null = null;

// Helper to get Nosana service by credential name
const getNosanaService = (credentialName?: string): NosanaService | null => {
    if (credentialName) {
        const service = nosanaServices.get(credentialName);
        if (service) return service;

        console.warn(`[Sidecar] Nosana Service '${credentialName}' requested but not found. Falling back to default service.`);
    }
    return defaultNosanaService;
};

// Helper to initialize/refresh a single Nosana service
const initNosanaService = async (
    name: string,
    privateKey: string | undefined,
    apiKey: string | undefined,
    rpc?: string
): Promise<NosanaService | null> => {
    if (!privateKey && !apiKey) {
        return null;
    }

    try {
        const mode = apiKey ? "API" : "WALLET";
        console.log(`[Sidecar] Initializing Nosana Service '${name}' in ${mode} mode...`);
        const service = new NosanaService({ privateKey, apiKey, rpcUrl: rpc });
        await service.init();
        console.log(`[Sidecar] Nosana Service '${name}' Initialized`);
        await service.recoverJobs();
        return service;
    } catch (e) {
        console.error(`[Sidecar] Failed to init Nosana Service '${name}':`, e);
        return null;
    }
};


// Initial Load
akashService.init().catch(err => console.error("Failed to init Akash:", err));


// --- Polling Logic (Fetch from Gateway) ---
let configFetchedOnce = false;

// Track credential fingerprints to detect changes (avoids unnecessary re-init)
const credentialFingerprints: Map<string, string> = new Map();

const computeFingerprint = (key?: string, apiKey?: string): string => {
    const raw = `${key || ''}:${apiKey || ''}`;
    return crypto.createHash('sha256').update(raw).digest('hex');
};

const fetchConfigFromGateway = async () => {
    try {
        // Use /config/credentials endpoint for unmasked credentials
        const url = `${API_GATEWAY_URL}/internal/config/credentials`;
        const response = await axios.get(url, {
            headers: {
                "X-Internal-API-Key": INTERNAL_API_KEY
            },
            timeout: 5000
        });

        const data = response.data;
        if (!data || !data.providers) return;

        const providers = data.providers;
        const depin = providers.depin || {};
        const nosanaConfig = depin.nosana || {};

        // --- Build the full credential map from config ---
        // This collects ALL credentials: legacy single key + api_keys list
        const desiredCredentials: Map<string, { privateKey?: string; apiKey?: string }> = new Map();

        // 1. Legacy single credential → "default"
        const legacyKey = nosanaConfig.wallet_private_key;
        const legacyApiKey = nosanaConfig.api_key;
        if (legacyKey || legacyApiKey) {
            desiredCredentials.set('default', {
                privateKey: legacyKey || undefined,
                apiKey: legacyApiKey || undefined,
            });
        }

        // 2. Named credentials from api_keys list
        const apiKeysList: Array<{ name: string; key: string; is_active?: boolean }> = nosanaConfig.api_keys || [];
        for (const entry of apiKeysList) {
            if (entry.is_active === false) continue; // Skip disabled credentials

            // Validate credential name
            if (!entry.name || entry.name.trim() === '') {
                console.warn(`[Sidecar] Skipping Nosana credential with empty name`);
                continue;
            }

            const credName = entry.name.trim();

            // Warn about reserved name collision
            if (credName === 'default') {
                if (desiredCredentials.has('default')) {
                    console.warn(`[Sidecar] Credential named 'default' conflicts with legacy config. Skipping api_keys entry.`);
                    continue;
                }
                console.warn(`[Sidecar] Using api_keys entry 'default' (no legacy config found).`);
            }

            // Skip duplicates within the api_keys list
            if (desiredCredentials.has(credName)) {
                console.warn(`[Sidecar] Duplicate credential name '${credName}' in api_keys. Keeping first occurrence.`);
                continue;
            }

            desiredCredentials.set(credName, {
                apiKey: entry.key,
            });
        }

        // --- Reconcile: Init new, update changed, remove stale ---
        const activeCredNames = new Set(desiredCredentials.keys());

        // Remove services for credentials that no longer exist
        for (const [name, service] of nosanaServices) {
            if (!activeCredNames.has(name)) {
                // Check for running jobs using this credential before removing
                const runningJobs = service.getWatchedJobsByCredential(name);
                if (runningJobs.length > 0) {
                    console.warn(`[Sidecar] WARNING: Removing Nosana Service '${name}' with ${runningJobs.length} running job(s): ${runningJobs.join(', ')}`);
                    console.warn(`[Sidecar] These jobs will continue but will not auto-redeploy. Consider stopping them manually.`);
                    // Mark jobs to skip auto-redeploy since credential is gone
                    for (const jobAddress of runningJobs) {
                        service.markJobForNoRedeploy(jobAddress);
                    }
                }

                console.log(`[Sidecar] Removing Nosana Service '${name}' (credential removed from config)`);
                nosanaServices.delete(name);
                credentialFingerprints.delete(name);
                if (name === 'default') {
                    defaultNosanaService = null;
                }
            }
        }

        // Initialize or update services for each credential
        for (const [name, cred] of desiredCredentials) {
            const newFingerprint = computeFingerprint(cred.privateKey, cred.apiKey);
            const existingFingerprint = credentialFingerprints.get(name);

            // Skip if credential hasn't changed and service already exists
            if (existingFingerprint === newFingerprint && nosanaServices.has(name)) {
                continue;
            }

            // Keep reference to old service in case init fails
            const oldService = nosanaServices.get(name);

            // Initialize (or re-initialize) this credential's service
            const service = await initNosanaService(
                name,
                cred.privateKey,
                cred.apiKey,
                process.env.SOLANA_RPC_URL
            );

            if (service) {
                nosanaServices.set(name, service);
                credentialFingerprints.set(name, newFingerprint);
                if (name === 'default') {
                    defaultNosanaService = service;
                }
            } else {
                // Init failed - log error and keep old service if it exists
                console.error(`[Sidecar] Failed to init Nosana Service '${name}'. ${oldService ? 'Keeping existing service.' : 'No fallback available.'}`);
                if (oldService) {
                    console.warn(`[Sidecar] Service '${name}' may be using stale credentials until next successful init.`);
                }
            }
        }

        // If no "default" service set but we have at least one, pick the first
        if (!defaultNosanaService && nosanaServices.size > 0) {
            const firstName = nosanaServices.keys().next().value;
            if (firstName) {
                defaultNosanaService = nosanaServices.get(firstName)!;
                console.log(`[Sidecar] No 'default' credential found. Using '${firstName}' as default service.`);
            }
        }

        if (nosanaServices.size === 0 && !configFetchedOnce) {
            console.warn("[Sidecar] No Nosana credentials configured. Nosana module disabled.");
        } else if (nosanaServices.size > 0) {
            console.log(`[Sidecar] Active Nosana credentials: [${Array.from(nosanaServices.keys()).join(', ')}]`);
        }

        // Refresh Akash if mnemonic changed
        const newAkashMnemonic = depin.akash?.mnemonic;
        if (newAkashMnemonic && newAkashMnemonic !== process.env.AKASH_MNEMONIC) {
            console.log("[Sidecar] Akash Mnemonic received from Gateway.");
            process.env.AKASH_MNEMONIC = newAkashMnemonic;
            akashService.init(newAkashMnemonic);
        }

        configFetchedOnce = true;

    } catch (e: any) {
        if (e.code === 'ECONNREFUSED') {
            console.warn("[Sidecar] Gateway unavailable. Retrying...");
        } else {
            console.error(`[Sidecar] Error fetching config: ${e.message}`);
        }
    }
};

// Start Polling
console.log("[Sidecar] Starting Config Polling (Interval: 10s)");
setInterval(fetchConfigFromGateway, 10000);
fetchConfigFromGateway(); // Initial run


// --- AKASH ROUTES ---
const akashRouter = express.Router();

akashRouter.post('/deployments/create', async (req, res) => {
    try {
        const { sdl, metadata } = req.body;
        if (!sdl) return res.status(400).json({ error: "Missing SDL" });
        const result = await akashService.createDeployment(sdl, metadata);
        res.json(result);
    } catch (error: any) {
        console.error("Akash Create Error:", error);
        res.status(500).json({ error: error.message });
    }
});

akashRouter.post('/deployments/close', async (req, res) => {
    try {
        const { deploymentId } = req.body;
        if (!deploymentId) return res.status(400).json({ error: "Missing deploymentId" });
        await akashService.closeDeployment(deploymentId);
        res.json({ success: true });
    } catch (error: any) {
        res.status(500).json({ error: error.message });
    }
});

akashRouter.get('/deployments/:id/logs', async (req, res) => {
    try {
        const logs = await akashService.getLogs(req.params.id);
        res.json({ logs });
    } catch (error: any) {
        res.status(500).json({ error: error.message });
    }
});

app.use('/akash', akashRouter);


// --- NOSANA ROUTES ---
const nosanaRouter = express.Router();

// Job state helper matches Watchdog logic
const isJobTerminated = (state: any): boolean => {
    // 2=COMPLETED, 3=STOPPED, 4=QUIT/FAILED in some versions
    return state === 2 || state === 3 || state === 4 || state === 'COMPLETED' || state === 'STOPPED';
};

// Middleware to check initialization
nosanaRouter.use((req, res, next) => {
    const credName = req.body?.credentialName || req.query?.credentialName;
    const service = getNosanaService(credName as string);
    if (!service) {
        return res.status(503).json({
            error: credName
                ? `Nosana Service '${credName}' not initialized`
                : "Nosana Service not initialized"
        });
    }
    (req as any).nosanaService = service;
    next();
});

nosanaRouter.get('/balance', async (req, res) => {
    try {
        const service = (req as any).nosanaService as NosanaService;
        const balance = await service.getBalance();
        res.json(balance);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

nosanaRouter.post('/jobs/launch', async (req, res) => {
    const { jobDefinition, marketAddress, resources_allocated, isConfidential = true, credentialName } = req.body;
    if (!jobDefinition || !marketAddress) return res.status(400).json({ error: "Missing definition/market" });

    try {
        const service = (req as any).nosanaService as NosanaService;
        const result = await service.launchJob(jobDefinition, marketAddress, isConfidential);

        // Watchdog
        service.watchJob(
            result.jobAddress,
            process.env.ORCHESTRATOR_URL || "http://localhost:8080",
            {
                jobDefinition,
                marketAddress,
                isConfidential,
                deploymentUuid: result.deploymentUuid,
                resources_allocated: resources_allocated || { gpu_allocated: 1, vcpu_allocated: 8, ram_gb_allocated: 32 },
                credentialName,
            }
        ).catch(console.error);

        res.json(result);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

nosanaRouter.post('/jobs/stop', async (req, res) => {
    const { jobAddress } = req.body;
    if (!jobAddress) return res.status(400).json({ error: "Missing jobAddress" });

    try {
        const service = (req as any).nosanaService as NosanaService;
        service.markJobAsStopping(jobAddress);
        const result = await service.stopJob(jobAddress);
        res.json(result);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

nosanaRouter.get('/jobs/:address', async (req, res) => {
    try {
        const service = (req as any).nosanaService as NosanaService;
        const result = await service.getJob(req.params.address);
        res.json(result);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

nosanaRouter.get('/jobs/:address/logs', async (req, res) => {
    try {
        const service = (req as any).nosanaService as NosanaService;
        const result = await service.getJobLogs(req.params.address);
        res.json(result);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

app.use('/nosana', nosanaRouter);


// --- GLOBAL HEALTH ---
app.get('/health', (req, res) => {
    res.json({
        status: "ok",
        service: "depin-sidecar",
        modules: {
            akash: "loaded",
            nosana: defaultNosanaService ? "active" : "disabled",
            credentials: Array.from(nosanaServices.keys())
        },
        config_source: "gateway-api"
    });
});

// --- WEBSOCKET LOG STREAMING ---
wss.on('connection', (ws: WebSocket) => {
    console.log("[WS] New client connected");
    let streamer: any = null;

    ws.on('message', async (message: string) => {
        try {
            const data = JSON.parse(message);

            if (data.type === 'subscribe_logs') {
                const { provider, jobId, nodeAddress, credentialName } = data;

                // Validate credentialName is a string if provided
                if (credentialName !== undefined && typeof credentialName !== 'string') {
                    ws.send(JSON.stringify({
                        type: 'error',
                        message: 'Invalid credentialName: must be a string'
                    }));
                    return;
                }

                if (provider === 'nosana') {
                    const service = getNosanaService(credentialName);
                    if (!service) {
                        ws.send(JSON.stringify({
                            type: 'error',
                            message: credentialName
                                ? `Nosana Service '${credentialName}' not initialized`
                                : 'Nosana Service not initialized'
                        }));
                        return;
                    }

                    try {
                        // 1. Check job state first
                        const job = await service.getJob(jobId);

                        if (isJobTerminated(job.jobState)) {
                            console.log(`[WS] Job ${jobId} is finished (State: ${job.jobState}). Fetching IPFS logs...`);
                            ws.send(JSON.stringify({ type: 'log', data: "[SYSTEM] Job has finished. Retrieving historical logs from IPFS..." }));

                            const logsData = await service.getJobLogs(jobId);
                            if (logsData.status === 'completed') {
                                const result = logsData.result;

                                // Helper to process and send logs
                                const sendLogs = (logs: any) => {
                                    if (Array.isArray(logs)) {
                                        logs.forEach(l => {
                                            const line = typeof l === 'string' ? l : (l.log || l.message || (l.logs ? null : JSON.stringify(l)));
                                            if (line) {
                                                ws.send(JSON.stringify({ type: 'log', data: line }));
                                            } else if (l.logs) {
                                                sendLogs(l.logs);
                                            }
                                        });
                                    }
                                };

                                if (result && typeof result === 'object') {
                                    const resAny = result as any;
                                    let foundLogs = false;

                                    if (resAny.opStates && Array.isArray(resAny.opStates)) {
                                        resAny.opStates.forEach((op: any) => {
                                            if (op.logs) {
                                                sendLogs(op.logs);
                                                foundLogs = true;
                                            }
                                        });
                                    } else if (resAny.logs) {
                                        sendLogs(resAny.logs);
                                        foundLogs = true;
                                    } else {
                                        sendLogs(result);
                                        foundLogs = true;
                                    }

                                    if (!foundLogs) {
                                        ws.send(JSON.stringify({ type: 'log', data: `[SYSTEM] Raw Result: ${JSON.stringify(result, null, 2)}` }));
                                    }
                                }

                                ws.send(JSON.stringify({ type: 'log', data: "[SYSTEM] --- END OF HISTORICAL LOGS ---" }));
                            } else {
                                ws.send(JSON.stringify({ type: 'log', data: "[SYSTEM] Historical logs are still being processed or not available." }));
                            }
                            return;
                        }

                        // 2. If running, use streamer
                        streamer = await service.getLogStreamer();

                        if (!streamer) {
                            // API mode - use polling-based log retrieval
                            console.log(`[WS] API mode detected - using polling for logs: ${jobId}`);
                            ws.send(JSON.stringify({ type: 'log', data: "[SYSTEM] Using API-based log retrieval (polling)..." }));

                            // Start polling for logs
                            const pollInterval = setInterval(async () => {
                                try {
                                    if (ws.readyState !== WebSocket.OPEN) {
                                        clearInterval(pollInterval);
                                        return;
                                    }

                                    const jobStatus = await service.getJob(jobId);

                                    if (isJobTerminated(jobStatus.jobState)) {
                                        // Job finished - get final logs
                                        ws.send(JSON.stringify({ type: 'log', data: "[SYSTEM] Job finished, fetching final results..." }));
                                        const logsData = await service.getJobLogs(jobId);
                                        if (logsData.status === 'completed') {
                                            const result = logsData.result;
                                            const sendLogs = (logs: any) => {
                                                if (Array.isArray(logs)) {
                                                    logs.forEach((l: any) => {
                                                        const line = typeof l === 'string' ? l : (l.log || l.message || (l.logs ? null : JSON.stringify(l)));
                                                        if (line) {
                                                            ws.send(JSON.stringify({ type: 'log', data: line }));
                                                        } else if (l.logs) {
                                                            sendLogs(l.logs);
                                                        }
                                                    });
                                                }
                                            };
                                            if (result && typeof result === 'object') {
                                                const resAny = result as any;
                                                if (resAny.opStates && Array.isArray(resAny.opStates)) {
                                                    resAny.opStates.forEach((op: any) => {
                                                        if (op.logs) sendLogs(op.logs);
                                                    });
                                                } else if (resAny.logs) {
                                                    sendLogs(resAny.logs);
                                                } else {
                                                    sendLogs(result);
                                                }
                                            }
                                        }
                                        ws.send(JSON.stringify({ type: 'log', data: "[SYSTEM] --- END OF LOGS ---" }));
                                        clearInterval(pollInterval);
                                        return;
                                    }

                                    // Job still running - just acknowledge
                                    ws.send(JSON.stringify({ type: 'log', data: `[SYSTEM] Job still running (state: ${jobStatus.jobState})...` }));

                                } catch (err: any) {
                                    console.error(`[WS] Polling error:`, err.message);
                                    ws.send(JSON.stringify({ type: 'error', message: err.message }));
                                    clearInterval(pollInterval);
                                }
                            }, 10000); // Poll every 10 seconds

                            // Store interval for cleanup
                            (ws as any).pollInterval = pollInterval;
                            return;
                        }

                        streamer.on('log', (log: any) => {
                            if (ws.readyState === WebSocket.OPEN) {
                                ws.send(JSON.stringify({ type: 'log', data: log }));
                            }
                        });

                        streamer.on('error', (err: Error) => {
                            if (ws.readyState === WebSocket.OPEN) {
                                ws.send(JSON.stringify({ type: 'error', message: err.message }));
                            }
                        });

                        const activeJobAddress = job.jobAddress || jobId;
                        const activeNodeAddress = nodeAddress || job.nodeAddress;

                        if (!activeNodeAddress) {
                            ws.send(JSON.stringify({ type: 'error', message: 'Node address is not available yet. The deployment might still be setting up.' }));
                            return;
                        }

                        console.log(`[WS] Subscribed to Nosana live logs: ${activeJobAddress} on node ${activeNodeAddress}`);
                        await streamer.connect(activeNodeAddress, activeJobAddress);
                    } catch (e: any) {
                        ws.send(JSON.stringify({ type: 'error', message: `Failed to initialize logs: ${e.message}` }));
                    }
                } else if (provider === 'akash') {
                    // Akash Log Streaming (Standardized placeholder)
                    ws.send(JSON.stringify({ type: 'log', data: { raw: 'Streaming logs for Akash is not yet supported via WebSocket.' } }));
                }
            }
        } catch (e) {
            console.error("[WS] Error handling message:", e);
        }
    });

    ws.on('close', () => {
        console.log("[WS] Client disconnected");
        if (streamer) {
            streamer.close();
            streamer = null;
        }
        // Clean up polling interval if exists
        if ((ws as any).pollInterval) {
            clearInterval((ws as any).pollInterval);
            (ws as any).pollInterval = null;
        }
    });
});

server.listen(PORT, '0.0.0.0', () => {
    console.log(`DePIN Sidecar (HTTP + WS) running on port ${PORT}`);
});
