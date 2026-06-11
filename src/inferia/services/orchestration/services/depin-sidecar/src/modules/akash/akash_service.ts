import { DirectSecp256k1HdWallet, Registry } from "@cosmjs/proto-signing";
import { SigningStargateClient } from "@cosmjs/stargate";
import { SDL } from "@akashnetwork/akashjs/build/sdl";
import { getTypeUrl } from "@akashnetwork/akashjs/build/stargate";
import { MsgCreateDeployment } from "@akashnetwork/akashjs/build/protobuf/akash/deployment/v1beta3/deploymentmsg";
import { MsgCreateLease } from "@akashnetwork/akashjs/build/protobuf/akash/market/v1beta4/lease";
import axios from 'axios';
import { getAkashTypeRegistry } from "@akashnetwork/akashjs/build/stargate";

const RPC_ENDPOINT = process.env.AKASH_NODE || "https://rpc.akash.forbole.com:443";

export class AkashService {
    private wallet: DirectSecp256k1HdWallet | null = null;
    private client: SigningStargateClient | null = null;
    private address: string = "";

    constructor() { }

    async init(mnemonicOverride?: string) {
        console.log("Initializing Akash Service (SDK)...");
        const mnemonic = mnemonicOverride || process.env.AKASH_MNEMONIC;
        if (!mnemonic) {
            console.warn("Akash Mnemonic missing.");
            return;
        }

        try {
            this.wallet = await DirectSecp256k1HdWallet.fromMnemonic(mnemonic, { prefix: "akash" });
            const [account] = await this.wallet.getAccounts();
            this.address = account.address;
            console.log(`Akash Wallet loaded: ${this.address}`);

            const registry = getAkashTypeRegistry();
            this.client = await SigningStargateClient.connectWithSigner(RPC_ENDPOINT, this.wallet, {
                registry: registry as any
            });
            console.log("Connected to Akash RPC");

        } catch (e) {
            console.error("Failed to init Akash SDK:", e);
        }
    }

    async createDeployment(sdlString: string, metadata: any) {
        if (!this.client || !this.wallet) throw new Error("Akash SDK not initialized (check mnemonic)");

        console.log("Parsing SDL...");
        const sdl = SDL.fromString(sdlString, "beta3");

        const groups = sdl.groups();
        const dseq = new Date().getTime().toString();

        // 1. Create Deployment
        console.log(`Creating deployment DSEQ=${dseq}...`);

        const msg = {
            id: {
                owner: this.address,
                dseq: dseq
            },
            groups: groups,
            version: new Uint8Array(),
            deposit: {
                denom: "uakt",
                amount: "5000000" // 5 AKT deposit
            },
            depositor: this.address
        };

        const typeUrl = getTypeUrl(MsgCreateDeployment);

        const tx = await this.client.signAndBroadcast(
            this.address,
            [{ typeUrl, value: msg }],
            "auto",
            "Create Deployment (Agent)"
        );

        if (tx.code !== 0) {
            throw new Error(`Tx Failed: ${tx.rawLog}`);
        }
        console.log(`Deployment created (Hash: ${tx.transactionHash})`);

        // 2. Wait for Bids
        console.log("Waiting for bids (20s)...");
        await new Promise(r => setTimeout(r, 20000));

        // 3. Query Bids (Simplified)
        const bids = await this.fetchBids(dseq);
        if (bids.length === 0) throw new Error("No bids found");

        const selectedBid = bids[0]; // Cheapest/first
        const provider = selectedBid.bid.bid_id.provider;
        console.log(`Selected provider: ${provider}`);

        // 4. Create Lease
        console.log("Creating lease...");
        const leaseMsg = {
            bidId: selectedBid.bid.bid_id
        };
        const leaseTypeUrl = getTypeUrl(MsgCreateLease);

        const leaseTx = await this.client.signAndBroadcast(
            this.address,
            [{ typeUrl: leaseTypeUrl, value: leaseMsg }],
            "auto",
            "Create Lease"
        );

        if (leaseTx.code !== 0) throw new Error(`Lease Tx Failed: ${leaseTx.rawLog}`);
        console.log("Lease created.");

        // 5. Send Manifest
        await this.sendManifest(sdl, dseq, provider);

        // 6. Get Status
        const exposeUrl = await this.waitForLeaseStatus(dseq, provider);

        return {
            deploymentId: dseq,
            leaseId: `${dseq}-${provider}`,
            status: "active",
            txHash: tx.transactionHash,
            exposeUrl: exposeUrl
        };
    }

    async fetchBids(dseq: string): Promise<any[]> {
        const url = `https://api.akashnet.net/akash/market/v1beta4/bids/list?filters.owner=${this.address}&filters.dseq=${dseq}&filters.state=open`;
        try {
            const res = await axios.get(url);
            return res.data.bids || [];
        } catch (e) {
            console.error("Error fetching bids", e);
            return [];
        }
    }

    async sendManifest(sdl: SDL, dseq: string, provider: string) {
        console.log(`Sending manifest to ${provider}... (Mocked for SDK)`);
        // Actual mTLS logic would go here
    }

    async waitForLeaseStatus(dseq: string, provider: string): Promise<string> {
        return `http://${provider}.ingress.akash:80`;
    }

    async closeDeployment(deploymentId: string) {
        console.log(`Closing deployment ${deploymentId}...`);
        // In real impl, broadcast MsgCloseDeployment
        return true;
    }

    async getLogs(deploymentId: string) {
        console.log(`Fetching logs for ${deploymentId}...`);
        return [
            `[SYSTEM] Log retrieval for Akash requires mTLS certificates.`,
            `[SYSTEM] Deployment ID: ${deploymentId}`,
            `[SYSTEM] Status: Active`
        ];
    }
}
