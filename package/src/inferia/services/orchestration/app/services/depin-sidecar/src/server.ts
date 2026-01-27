import express from 'express';
import http from 'http';
import { WebSocketServer, WebSocket } from 'ws';
import dotenv from 'dotenv';
import cors from 'cors';
import axios from 'axios';
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
const FILTRATION_URL = process.env.FILTRATION_URL || "http://localhost:8000";
const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || "dev-internal-key-change-in-prod";

console.log(`[Sidecar] Configured to fetch settings from: ${FILTRATION_URL}`);

// --- Initialize Services ---
const akashService = new AkashService();
let nosanaService: NosanaService | null = null;

// Helper to initialize/refresh Nosana
const initNosana = async (key: string | undefined, rpc?: string) => {
    if (!key) {
        console.warn("[Sidecar] Nosana key missing. Nosana module disabled.");
        nosanaService = null;
        return;
    }
    // Avoid re-init if key hasn't changed (assumes NosanaService stores it)
    if (nosanaService && (nosanaService as any).privateKey === key) {
        return; 
    }

    try {
        console.log("[Sidecar] Initializing Nosana Service...");
        nosanaService = new NosanaService(key, rpc);
        await nosanaService.init();
        console.log("[Sidecar] Nosana Service Wallet Initialized");
        await nosanaService.recoverJobs();
    } catch (e) {
        console.error("[Sidecar] Failed to init Nosana Service:", e);
        nosanaService = null;
    }
};

// Initial Load (will be updated by poll immediately)
akashService.init().catch(err => console.error("Failed to init Akash:", err));
initNosana(process.env.NOSANA_WALLET_PRIVATE_KEY, process.env.SOLANA_RPC_URL);


// --- Polling Logic (Fetch from Gateway) ---
const fetchConfigFromGateway = async () => {
    try {
        const url = `${FILTRATION_URL}/internal/config/provider`;
        const response = await axios.get(url, {
            headers: {
                "X-Internal-Key": INTERNAL_API_KEY
            },
            timeout: 5000
        });

        const data = response.data;
        if (!data || !data.providers) return;

        const providers = data.providers;
        const depin = providers.depin || {};

        // Refresh Nosana if key changed
        const newNosanaKey = depin.nosana?.wallet_private_key;
        if (newNosanaKey && newNosanaKey !== process.env.NOSANA_WALLET_PRIVATE_KEY) {
            console.log("[Sidecar] Nosana key received from Gateway.");
            process.env.NOSANA_WALLET_PRIVATE_KEY = newNosanaKey;
            initNosana(newNosanaKey, process.env.SOLANA_RPC_URL);
        }

        // Refresh Akash if mnemonic changed
        const newAkashMnemonic = depin.akash?.mnemonic;
        if (newAkashMnemonic && newAkashMnemonic !== process.env.AKASH_MNEMONIC) {
            console.log("[Sidecar] Akash Mnemonic received from Gateway.");
            process.env.AKASH_MNEMONIC = newAkashMnemonic;
            akashService.init(newAkashMnemonic);
        }

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
    if (!nosanaService) return res.status(503).json({ error: "Nosana Service not initialized" });
    next();
});

nosanaRouter.get('/balance', async (req, res) => {
    try {
        const balance = await nosanaService!.getBalance();
        res.json(balance);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

nosanaRouter.post('/jobs/launch', async (req, res) => {
    const { jobDefinition, marketAddress, resources_allocated } = req.body;
    if (!jobDefinition || !marketAddress) return res.status(400).json({ error: "Missing definition/market" });

    try {
        const result = await nosanaService!.launchJob(jobDefinition, marketAddress);

        // Watchdog
        nosanaService!.watchJob(
            result.jobAddress,
            process.env.ORCHESTRATOR_URL || "http://localhost:8080",
            {
                jobDefinition,
                marketAddress,
                resources_allocated: resources_allocated || { gpu_allocated: 1, vcpu_allocated: 8, ram_gb_allocated: 32 }
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
        nosanaService!.markJobAsStopping(jobAddress);
        const result = await nosanaService!.stopJob(jobAddress);
        res.json(result);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

nosanaRouter.get('/jobs/:address', async (req, res) => {
    try {
        const result = await nosanaService!.getJob(req.params.address);
        res.json(result);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

nosanaRouter.get('/jobs/:address/logs', async (req, res) => {
    try {
        const result = await nosanaService!.getJobLogs(req.params.address);
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
            nosana: nosanaService ? "active" : "disabled"
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
                const { provider, jobId, nodeAddress } = data;

                if (provider === 'nosana') {
                    if (!nosanaService) {
                        ws.send(JSON.stringify({ type: 'error', message: 'Nosana Service not initialized' }));
                        return;
                    }

                    try {
                        // 1. Check job state first
                        const job = await nosanaService.getJob(jobId);

                        if (isJobTerminated(job.jobState)) {
                            console.log(`[WS] Job ${jobId} is finished (State: ${job.jobState}). Fetching IPFS logs...`);
                            ws.send(JSON.stringify({ type: 'log', data: "[SYSTEM] Job has finished. Retrieving historical logs from IPFS..." }));

                            const logsData = await nosanaService.getJobLogs(jobId);
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
                                    if (resAny.opStates && Array.isArray(resAny.opStates)) {
                                        resAny.opStates.forEach((op: any) => {
                                            if (op.logs) sendLogs(op.logs);
                                        });
                                    } else {
                                        sendLogs(result);
                                    }
                                }

                                ws.send(JSON.stringify({ type: 'log', data: "[SYSTEM] --- END OF HISTORICAL LOGS ---" }));
                            } else {
                                ws.send(JSON.stringify({ type: 'log', data: "[SYSTEM] Historical logs are still being processed or not available." }));
                            }
                            return;
                        }

                        // 2. If running, use streamer
                        streamer = await nosanaService.getLogStreamer();

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

                        console.log(`[WS] Subscribed to Nosana live logs: ${jobId} on node ${nodeAddress}`);
                        await streamer.connect(nodeAddress, jobId);
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
    });
});

server.listen(PORT, '0.0.0.0', () => {
    console.log(`DePIN Sidecar (HTTP + WS) running on port ${PORT}`);
});
