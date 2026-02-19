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

export interface NosanaApiKeyEntry {
    name: string;
    key: string;
    is_active: boolean;
}

export interface NosanaConfig {
    wallet_private_key?: string;
    api_key?: string;  // Deprecated: kept for migration
    api_keys?: NosanaApiKeyEntry[];
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

// Universal Provider Credential Types (works for ANY provider)
export interface ProviderCredentialResponse {
    provider: string;
    name: string;
    credential_type: string;  // e.g., 'api_key', 'wallet', 'mnemonic', 'access_key'
    is_active: boolean;
    created_at?: string;
}

export interface ProviderCredentialListResponse {
    credentials: ProviderCredentialResponse[];
}

// Legacy types for backward compatibility
export interface NosanaApiKeyResponse {
    name: string;
    is_active: boolean;
    created_at?: string;
}

export interface NosanaApiKeyListResponse {
    api_keys: NosanaApiKeyResponse[];
}

export const ConfigService = {
    async getProviderConfig(): Promise<ProvidersConfig> {
        const { data } = await api.get<ProviderConfigResponse>('/management/config/providers');
        return data.providers;
    },

    async updateProviderConfig(config: ProvidersConfig): Promise<void> {
        // Wrap in parent object to match backend expectation
        await api.post('/management/config/providers', { providers: config });
    },

    // Universal Provider Credential Management (works for ANY provider: nosana, akash, aws, etc.)
    async listProviderCredentials(provider: string): Promise<ProviderCredentialResponse[]> {
        const { data } = await api.get<ProviderCredentialListResponse>(`/management/config/providers/${provider}/credentials`);
        return data.credentials;
    },

    async addProviderCredential(
        provider: string, 
        name: string, 
        credentialType: string, 
        value: string
    ): Promise<{ provider: string; name: string }> {
        const { data } = await api.post(`/management/config/providers/${provider}/credentials`, { 
            name, 
            credential_type: credentialType, 
            value 
        });
        return data;
    },

    async updateProviderCredential(
        provider: string, 
        name: string, 
        updates: { credential_type?: string; value?: string; is_active?: boolean }
    ): Promise<void> {
        await api.put(`/management/config/providers/${provider}/credentials/${name}`, updates);
    },

    async deleteProviderCredential(provider: string, name: string): Promise<void> {
        await api.delete(`/management/config/providers/${provider}/credentials/${name}`);
    },

    // Legacy convenience methods for backward compatibility
    async listNosanaApiKeys(): Promise<NosanaApiKeyResponse[]> {
        const credentials = await this.listProviderCredentials('nosana');
        // Filter only api_key type credentials and map to legacy format
        return credentials
            .filter(c => c.credential_type === 'api_key')
            .map(c => ({
                name: c.name,
                is_active: c.is_active,
                created_at: c.created_at
            }));
    },

    async addNosanaApiKey(name: string, key: string): Promise<{ name: string }> {
        return this.addProviderCredential('nosana', name, 'api_key', key);
    },

    async updateNosanaApiKey(name: string, updates: { key?: string; is_active?: boolean }): Promise<void> {
        const mappedUpdates: any = {};
        if (updates.key !== undefined) mappedUpdates.value = updates.key;
        if (updates.is_active !== undefined) mappedUpdates.is_active = updates.is_active;
        return this.updateProviderCredential('nosana', name, mappedUpdates);
    },

    async deleteNosanaApiKey(name: string): Promise<void> {
        return this.deleteProviderCredential('nosana', name);
    }
};
