import api from "@/lib/api";

export interface AWSConfig {
    access_key_id?: string;
    secret_access_key?: string;
    region?: string;
}

export interface ChromaConfig {
    api_key?: string;
    tenant?: string;
    url?: string;
    is_local?: boolean;
    database?: string;
}

export interface GroqConfig {
    api_key?: string;
}

export interface LakeraConfig {
    api_key?: string;
}

export interface NosanaConfig {
    wallet_private_key?: string;
    api_key?: string;
}

export interface AkashConfig {
    mnemonic?: string;
}

export interface CloudConfig {
    aws: AWSConfig;
}

export interface VectorDBConfig {
    chroma: ChromaConfig;
}

export interface GuardrailsConfig {
    groq: GroqConfig;
    lakera: LakeraConfig;
}

export interface DePINConfig {
    nosana: NosanaConfig;
    akash: AkashConfig;
}

export interface ProvidersConfig {
    cloud: CloudConfig;
    vectordb: VectorDBConfig;
    guardrails: GuardrailsConfig;
    depin: DePINConfig;
}

export interface ProviderConfigResponse {
    providers: ProvidersConfig;
}

// Initial state helper
export const initialProviderConfig: ProvidersConfig = {
    cloud: { aws: {} },
    vectordb: { chroma: { is_local: true } },
    guardrails: { groq: {}, lakera: {} },
    depin: { nosana: {}, akash: {} }
};

export const ConfigService = {
    async getProviderConfig(): Promise<ProvidersConfig> {
        const { data } = await api.get<ProviderConfigResponse>('/management/config/providers');
        return data.providers;
    },

    async updateProviderConfig(config: ProvidersConfig): Promise<void> {
        // Wrap in parent object to match backend expectation
        await api.post('/management/config/providers', { providers: config });
    }
};
