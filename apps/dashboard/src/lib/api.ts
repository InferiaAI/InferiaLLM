import axios, { type AxiosInstance } from "axios";

// Runtime config injected via /config.js at container startup.
// Falls back to VITE_ build-time env vars, then to localhost defaults.
const rc = (window as any).__RUNTIME_CONFIG__ || {};

// URLs - All through API Gateway
export const API_GATEWAY_URL = rc.API_GATEWAY_URL || import.meta.env.VITE_API_GATEWAY_URL || "http://localhost:8000";

// Service URLs for health checks and direct access
// API Gateway (replaces MANAGEMENT_URL)
export const MANAGEMENT_URL = API_GATEWAY_URL;
// Orchestration now accessed through API Gateway proxy
export const COMPUTE_URL = `${API_GATEWAY_URL}/api/v1`;
// Inference Gateway - separate public service
export const INFERENCE_URL = rc.INFERENCE_URL || import.meta.env.VITE_INFERENCE_URL || "http://localhost:8001";
// Internal services - accessed through gateway
export const DATA_URL = API_GATEWAY_URL;
export const GUARDRAIL_URL = API_GATEWAY_URL;
// WebSocket still goes through sidecar for DePIN
export const WEB_SOCKET_URL = rc.WEB_SOCKET_URL || import.meta.env.VITE_WEB_SOCKET_URL || "ws://localhost:3000";
export const SIDECAR_URL = rc.SIDECAR_URL || import.meta.env.VITE_SIDECAR_URL || "http://localhost:3000";

// Client Factory
const createApiClient = (baseURL: string): AxiosInstance => {
    const instance = axios.create({
        baseURL,
        headers: {
            "Content-Type": "application/json",
        },
    });

    // Request Interceptor: Attach Token
    instance.interceptors.request.use(
        (config) => {
            const token = localStorage.getItem("token");
            if (token) {
                config.headers.Authorization = `Bearer ${token}`;
            }
            return config;
        },
        (error) => Promise.reject(error)
    );

    // Response Interceptor: Handle 401
    instance.interceptors.response.use(
        (response) => response,
        (error) => {
            if (error.response?.status === 401) {
                // Check current path to avoid redirect loop
                if (window.location.pathname !== "/auth/login") {
                    localStorage.removeItem("token");
                    window.location.href = "/auth/login";
                }
            }
            return Promise.reject(error);
        }
    );

    return instance;
};

// Single unified API client - all requests go through API Gateway
export const api = createApiClient(API_GATEWAY_URL);

// Convenience exports for different API domains (all proxied through gateway)
export const authApi = createApiClient(`${API_GATEWAY_URL}/auth`);
export const managementApi = createApiClient(`${API_GATEWAY_URL}/management`);
export const adminApi = createApiClient(`${API_GATEWAY_URL}/admin`);
export const auditApi = createApiClient(`${API_GATEWAY_URL}/audit`);

// Compute API - uses gateway proxy routes to orchestration service
// This replaces direct orchestration calls with gateway-proxied requests
export const computeApi = createApiClient(`${API_GATEWAY_URL}/api/v1`);

// Deployment API - uses gateway proxy routes to orchestration service
export const deploymentApi = createApiClient(`${API_GATEWAY_URL}/api/v1/deployments`);
export const poolApi = createApiClient(`${API_GATEWAY_URL}/api/v1/pools`);
export const insightApi = createApiClient(`${API_GATEWAY_URL}/api/v1/insights`);

// Default export alias for backward compatibility
export default api;
