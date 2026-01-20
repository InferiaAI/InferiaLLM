import express from 'express';
import dotenv from 'dotenv';
import cors from 'cors';
import { NosanaService } from './nosana_service';

dotenv.config();

const app = express();
const PORT: number = Number(process.env.PORT) || 3000;

app.use(express.json());
app.use(cors());

// Initialize Service
const privateKey = process.env.NOSANA_WALLET_PRIVATE_KEY;
const rpcUrl = process.env.SOLANA_RPC_URL;

if (!privateKey) {
    console.warn("WARNING: NOSANA_WALLET_PRIVATE_KEY is not set. Sidecar will fail to initialize client.");
} else {
    // console.log("Initializing Nosana Service with private key length:", privateKey.length);
}

let nosanaService: NosanaService;

try {
    if (privateKey) {
        nosanaService = new NosanaService(privateKey, rpcUrl);
        // Initialize async wallet
        nosanaService.init().then(async () => {
            console.log("Nosana Service Wallet Initialized");
            await nosanaService.recoverJobs();
        }).catch(err => {
            console.error("Failed to initialize Nosana Wallet:", err);
        });
    }
} catch (e) {
    console.error("Failed to initialize NosanaService:", e);
}

// Routes
app.get('/health', (req, res) => {
    res.json({ status: 'ok', service_initialized: !!nosanaService });
});

app.get('/balance', async (req, res) => {
    if (!nosanaService) return res.status(503).json({ error: "NosanaService not initialized" });
    try {
        const balance = await nosanaService.getBalance();
        res.json(balance);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/jobs/launch', async (req, res) => {
    if (!nosanaService) return res.status(503).json({ error: "NosanaService not initialized" });
    const { jobDefinition, marketAddress, resources_allocated } = req.body;

    if (!jobDefinition || !marketAddress) {
        return res.status(400).json({ error: "Missing jobDefinition or marketAddress" });
    }

    try {
        const result = await nosanaService.launchJob(jobDefinition, marketAddress);

        // Start watching with full job metadata for auto-extend and auto-redeploy
        nosanaService
            .watchJob(
                result.jobAddress,
                process.env.ORCHESTRATOR_URL || "http://localhost:8080",
                {
                    jobDefinition,
                    marketAddress,
                    resources_allocated: resources_allocated || {
                        gpu_allocated: 1,
                        vcpu_allocated: 8,
                        ram_gb_allocated: 32
                    }
                }
            )
            .catch(console.error);

        res.json(result);
    } catch (e: any) {
        console.error("Launch API Error:", e);
        res.status(500).json({ error: e.message });
    }
});

app.post('/jobs/stop', async (req, res) => {
    if (!nosanaService) return res.status(503).json({ error: "NosanaService not initialized" });
    const { jobAddress } = req.body;
    if (!jobAddress) return res.status(400).json({ error: "Missing jobAddress" });

    try {
        // Mark as user-stopped BEFORE calling stop to prevent auto-redeploy
        nosanaService.markJobAsStopping(jobAddress);

        const result = await nosanaService.stopJob(jobAddress);
        res.json(result);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

app.get('/jobs/:address', async (req, res) => {
    if (!nosanaService) return res.status(503).json({ error: "NosanaService not initialized" });
    const { address } = req.params;
    try {
        const result = await nosanaService.getJob(address);
        res.json(result);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

app.get('/jobs/:address/logs', async (req, res) => {
    if (!nosanaService) return res.status(503).json({ error: "NosanaService not initialized" });
    const { address } = req.params;
    try {
        const result = await nosanaService.getJobLogs(address);
        res.json(result);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});


app.listen(PORT, '0.0.0.0', () => {
    console.log(`Nosana Sidecar running at http://localhost:${PORT}`);
});
