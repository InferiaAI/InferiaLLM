import express from 'express';
import dotenv from 'dotenv';
import cors from 'cors';
import fs from 'fs';
import path from 'path';
import os from 'os';
import { AkashService } from './modules/akash/akash_service';
import { NosanaService } from './modules/nosana/nosana_service';

dotenv.config();

const app = express();
const PORT: number = Number(process.env.PORT) || 3000;

app.use(express.json());
app.use(cors());

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

// Initial Load
akashService.init().catch(err => console.error("Failed to init Akash:", err));
initNosana(process.env.NOSANA_WALLET_PRIVATE_KEY, process.env.SOLANA_RPC_URL);

// --- Hot Reload Logic (Watch config.json) ---
const configPath = path.join(os.homedir(), '.inferia', 'config.json');

const loadConfig = () => {
    if (!fs.existsSync(configPath)) return;
    try {
        console.log("[Sidecar] Shared config change detected. Refreshing credentials...");
        const data = JSON.parse(fs.readFileSync(configPath, 'utf8'));
        const providers = data.providers || {};
        const depin = providers.depin || {};

        // Refresh Nosana if key changed
        const newNosanaKey = depin.nosana?.wallet_private_key;
        if (newNosanaKey && newNosanaKey !== process.env.NOSANA_WALLET_PRIVATE_KEY) {
            process.env.NOSANA_WALLET_PRIVATE_KEY = newNosanaKey;
            initNosana(newNosanaKey, process.env.SOLANA_RPC_URL);
        }

        // Refresh Akash if mnemonic changed
        const newAkashMnemonic = depin.akash?.mnemonic;
        if (newAkashMnemonic && newAkashMnemonic !== process.env.AKASH_MNEMONIC) {
            process.env.AKASH_MNEMONIC = newAkashMnemonic;
            console.log("[Sidecar] Akash Mnemonic updated. Re-initializing Akash SDK...");
            akashService.init(newAkashMnemonic);
        }
    } catch (e) {
        console.error("[Sidecar] Error reloading config:", e);
    }
};

// Start watching the config file
if (fs.existsSync(configPath)) {
    fs.watch(configPath, (event) => {
        if (event === 'change') {
            // Debounce or just load
            loadConfig();
        }
    });
}


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
        config_watch: fs.existsSync(configPath) ? "active" : "failed"
    });
});

app.listen(PORT, '0.0.0.0', () => {
    console.log(`DePIN Sidecar running on port ${PORT}`);
});
