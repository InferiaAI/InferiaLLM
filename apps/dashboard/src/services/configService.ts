import api, { computeApi } from "@/lib/api";

export interface AWSConfig {
    access_key_id?: string;
    secret_access_key?: string;
    region?: string;
    // Account-wide provisioning defaults (formerly per-pool metadata).
    // Pulumi reads these when creating EC2 clusters. All optional —
    // leaving them blank lets Pulumi pick sane defaults.
    subnet_id?: string;
    security_group_ids?: string[];
    ami_id?: string;
    iam_instance_profile?: string;
    root_volume_gb?: number;
    worker_image_tag?: string;
}

export interface GCPConfig {
    project_id?: string;
    service_account_json?: string;
    region?: string;
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
    gcp: GCPConfig;
}

export interface DePINConfig {
    nosana: NosanaConfig;
    akash: AkashConfig;
}

export interface HuggingFaceConfig {
    token?: string;
    tokens?: { name: string; token: string; is_active?: boolean }[];
}

export interface ProvidersConfig {
    cloud: CloudConfig;
    depin: DePINConfig;
    huggingface: HuggingFaceConfig;
}

export interface ProviderConfigResponse {
    providers: ProvidersConfig;
    hf_token_from_env: boolean;
}

// Initial state helper
export const initialProviderConfig: ProvidersConfig = {
    cloud: { aws: {}, gcp: {} },
    depin: { nosana: {}, akash: {} },
    huggingface: { token: "", tokens: [] }
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

// HuggingFace named tokens (managed via the universal credential system,
// like Nosana API keys). Values are never returned by the API.
export interface HfTokenResponse {
    name: string;
    is_active: boolean;
    created_at?: string;
}

export const ConfigService = {
    async getProviderConfig(): Promise<ProvidersConfig> {
        const { data } = await api.get<ProviderConfigResponse>('/management/config/providers');
        return data.providers;
    },

    async getProviderConfigFull(): Promise<ProviderConfigResponse> {
        const { data } = await api.get<ProviderConfigResponse>('/management/config/providers');
        return data;
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
    },

    // HuggingFace named tokens — same universal credential system as Nosana
    // (credential_type 'token'). Values are write-only: never returned on list.
    async listHfTokens(): Promise<HfTokenResponse[]> {
        const credentials = await this.listProviderCredentials('huggingface');
        return credentials
            .filter(c => c.credential_type === 'token')
            .map(c => ({
                name: c.name,
                is_active: c.is_active,
                created_at: c.created_at,
            }));
    },

    async addHfToken(name: string, token: string): Promise<{ name: string }> {
        return this.addProviderCredential('huggingface', name, 'token', token);
    },

    async deleteHfToken(name: string): Promise<void> {
        return this.deleteProviderCredential('huggingface', name);
    },

    // Engine-cache AMI admin endpoints (gateway proxy → /api/v1/admin/aws/engine-ami)
    async listEngineAmis(region: string): Promise<{ ami_id: string; vllm_tag?: string; region: string; created: string }[]> {
        const { data } = await computeApi.get<{ amis: { ami_id: string; vllm_tag?: string; region: string; created: string }[] }>('/admin/aws/engine-ami', { params: { region } });
        return data.amis;
    },

    async startEngineBake(body: { region: string; vllm_tag?: string }): Promise<{ bake_id: string; status: string }> {
        const { data } = await computeApi.post<{ bake_id: string; status: string }>('/admin/aws/engine-ami/bake', body);
        return data;
    },

    async pollBakeStatus(bakeId: string): Promise<{ status: string; message: string; ami_id?: string; region: string }> {
        const { data } = await computeApi.get<{ status: string; message: string; ami_id?: string; region: string }>(`/admin/aws/engine-ami/bake/${bakeId}`);
        return data;
    },

    async listHfTokenNames(): Promise<string[]> {
        const { data } = await api.get<{ names: string[] }>('/management/config/providers/huggingface/token-names');
        return data.names;
    },
};
