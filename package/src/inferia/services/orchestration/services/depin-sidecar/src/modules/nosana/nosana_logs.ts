import WebSocket from 'ws';
import bs58 from 'bs58';
import nacl from 'tweetnacl';
import { EventEmitter } from 'events';

const FRP_SERVER_ADDR = 'node.k8s.prd.nos.ci';
const SIGN_MESSAGE = 'Hello Nosana Node!';

// Type for API auth provider function
export type ApiAuthProvider = () => Promise<{ header: string; userAddress: string }>;

export class LogStreamer extends EventEmitter {
    private ws: WebSocket | null = null;
    private shouldReconnect: boolean = true;
    private retryCount: number = 0;
    private maxRetries: number = 10;
    private retryDelay: number = 3000;
    private authMode: 'wallet' | 'api';
    private walletSigner: any;

    /**
     * Create a new LogStreamer
     * @param walletSigner - Wallet signer (for wallet mode) or null (for API mode)
     */
    constructor(walletSigner: any = null) {
        super();

        if (!walletSigner) {
            // API mode uses ephemeral wallet
            this.authMode = 'api';
            this.walletSigner = null;
        } else {
            // Wallet mode
            this.authMode = 'wallet';
            this.walletSigner = walletSigner;
        }
    }

    /**
     * Generate authorization header with signed message (wallet mode)
     */
    private async generateWalletAuth(): Promise<{ auth: string; walletAddress: string }> {
        if (!this.walletSigner) {
            throw new Error('Wallet signer not available');
        }

        const message = SIGN_MESSAGE;

        // Get the keypair from the signer
        const publicKey = this.walletSigner.address.toString();

        // Sign the message
        const messageBytes = new TextEncoder().encode(message);

        // For @solana/kit signer, we need to pass an object with content
        const signatures = await this.walletSigner.signMessages([{ content: messageBytes }]);
        const signatureResult = Object.values(signatures[0])[0] as Uint8Array;
        const signature = bs58.encode(signatureResult);

        // Format: MESSAGE:SIGNATURE
        return {
            auth: `${message}:${signature}`,
            walletAddress: publicKey
        };
    }

    /**
     * Generate authorization header using ephemeral keypair (API mode)
     */
    private async generateEphemeralAuth(): Promise<{ auth: string; walletAddress: string }> {
        const kp = nacl.sign.keyPair();
        const address = bs58.encode(kp.publicKey);
        const signatureBytes = nacl.sign.detached(Buffer.from(SIGN_MESSAGE), kp.secretKey);
        const signature = bs58.encode(signatureBytes);

        return {
            auth: `${SIGN_MESSAGE}:${signature}`,
            walletAddress: address
        };
    }

    /**
     * Generate authorization header (auto-detects mode)
     */
    async generateAuth(): Promise<{ auth: string; walletAddress: string }> {
        if (this.authMode === 'api') {
            return this.generateEphemeralAuth();
        } else {
            return this.generateWalletAuth();
        }
    }

    /**
     * Connect to node's WebSocket and start streaming logs
     * @param nodeAddress - The node's public key running the job
     * @param jobAddress - The job address to stream logs for
     */
    async connect(nodeAddress: string, jobAddress: string): Promise<void> {
        const url = `wss://${nodeAddress}.${FRP_SERVER_ADDR}`;
        console.log(`[LogStreamer] Connecting to ${url} (mode: ${this.authMode})`);

        return new Promise((resolve, reject) => {
            this.ws = new WebSocket(url);

            this.ws.on('open', async () => {
                console.log(`[LogStreamer] Connected to node ${nodeAddress}`);
                this.retryCount = 0;

                try {
                    const { auth, walletAddress } = await this.generateAuth();

                    const subscribeMessage = {
                        path: '/log',
                        body: {
                            jobAddress: jobAddress,
                            address: walletAddress
                        },
                        header: auth
                    };

                    console.log(`[LogStreamer] Subscribing to logs for job ${jobAddress} (wallet: ${walletAddress})`);
                    this.ws?.send(JSON.stringify(subscribeMessage));
                    resolve();
                } catch (err) {
                    console.error('[LogStreamer] Failed to send subscribe message:', err);
                    reject(err);
                }
            });

            this.ws.on('message', (data) => {
                try {
                    const message = JSON.parse(data.toString());

                    if (message.path === 'log') {
                        // Parse the log data
                        const logData = JSON.parse(message.data);
                        this.emit('log', logData);
                    } else if (message.error) {
                        this.emit('error', new Error(message.error));
                    }
                } catch (err) {
                    // Raw log line
                    this.emit('log', { raw: data.toString() });
                }
            });

            this.ws.on('error', (err) => {
                console.error('[LogStreamer] WebSocket error:', err.message);
                this.emit('error', err);
            });

            this.ws.on('close', (code, reason) => {
                // Ignore 1005 (No Status) and 1000 (Normal) if we are about to close anyway or if it's expected
                if (code !== 1000 && code !== 1005) {
                    console.log(`[LogStreamer] WebSocket closed. Code: ${code}, Reason: ${reason}`);
                }

                if (this.shouldReconnect && this.retryCount < this.maxRetries) {
                    this.retryCount++;
                    console.log(`[LogStreamer] Reconnecting in ${this.retryDelay}ms (attempt ${this.retryCount}/${this.maxRetries})`);
                    setTimeout(() => this.connect(nodeAddress, jobAddress), this.retryDelay);
                } else {
                    this.emit('close');
                }
            });

            // Timeout for initial connection
            setTimeout(() => {
                // Check if ws exists and is NOT open
                if (this.ws && (this.ws.readyState === WebSocket.CONNECTING)) {
                    reject(new Error('WebSocket connection timeout'));
                }
            }, 10000);
        });
    }

    /**
     * Close the WebSocket connection
     */
    close() {
        this.shouldReconnect = false;
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
}
