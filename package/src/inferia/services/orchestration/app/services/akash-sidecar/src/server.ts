import express from 'express';
import { AkashService } from './akash_service';

const app = express();
const port = process.env.PORT || 3001;
const akashService = new AkashService();

app.use(express.json());

// ---------------------------------------------
// DEPLOYMENTS
// ---------------------------------------------
app.post('/deployments/create', async (req, res) => {
    try {
        const { sdl, metadata } = req.body;
        if (!sdl) {
            return res.status(400).json({ error: "Missing SDL definition" });
        }

        const result = await akashService.createDeployment(sdl, metadata);
        res.json(result);
    } catch (error: any) {
        console.error("Create deployment failed:", error);
        res.status(500).json({ error: error.message });
    }
});

app.post('/deployments/close', async (req, res) => {
    try {
        const { deploymentId } = req.body;
        if (!deploymentId) {
            return res.status(400).json({ error: "Missing deploymentId" });
        }

        await akashService.closeDeployment(deploymentId);
        res.json({ success: true });
    } catch (error: any) {
        console.error("Close deployment failed:", error);
        res.status(500).json({ error: error.message });
    }
});

app.get('/deployments/:id/logs', async (req, res) => {
    try {
        const deploymentId = req.params.id;
        const logs = await akashService.getLogs(deploymentId);
        res.json({ logs });
    } catch (error: any) {
        console.error("Fetch logs failed:", error);
        res.status(500).json({ error: error.message });
    }
});

// ---------------------------------------------
// HEALTH / STATUS
// ---------------------------------------------
app.get('/health', (req, res) => {
    res.json({ status: "ok", service: "akash-sidecar" });
});

app.listen(port, () => {
    console.log(`Akash Sidecar running on port ${port}`);
    akashService.init().catch(console.error);
});
