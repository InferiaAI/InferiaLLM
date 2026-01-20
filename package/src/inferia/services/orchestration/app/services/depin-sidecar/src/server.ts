import express from 'express';
import dotenv from 'dotenv';
import cors from 'cors';
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

// Akash Init
akashService.init().catch(err => console.error("Failed to init Akash:", err));

// Nosana Init
const nosanaKey = process.env.NOSANA_WALLET_PRIVATE_KEY;
const nosanaRpc = process.env.SOLANA_RPC_URL;

if (nosanaKey) {
    try {
        nosanaService = new NosanaService(nosanaKey, nosanaRpc);
        nosanaService.init().then(async () => {
            console.log("Nosana Service Wallet Initialized");
            await nosanaService!.recoverJobs();
        }).catch(err => console.error("Failed to init Nosana Wallet:", err));
    } catch (e) {
        console.error("Failed to create NosanaService:", e);
    }
} else {
    console.warn("NOSANA_WALLET_PRIVATE_KEY missing. Nosana module disabled.");
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
        }
    });
});

app.listen(PORT, '0.0.0.0', () => {
    console.log(`DePIN Sidecar running on port ${PORT}`);
});
