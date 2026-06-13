import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from "axios";
import { toast } from "sonner";
import {
    getToken,
    setToken,
    clearToken,
    getRefreshToken,
    setRefreshToken,
} from "@/lib/tokenStore";
import { isExternalAuthMode } from "@/lib/authMode";

// Runtime config injected via /config.js at container startup.
// Falls back to VITE_ build-time env vars, then to localhost defaults.
const rc = (window as unknown as { __RUNTIME_CONFIG__?: Record<string, string> }).__RUNTIME_CONFIG__ || {};

// URLs - All through API Gateway
export const API_GATEWAY_URL = rc.API_GATEWAY_URL || import.meta.env.VITE_API_GATEWAY_URL || "http://localhost:8000";

// Service URLs for health checks and direct access
// API Gateway (replaces MANAGEMENT_URL)
export const MANAGEMENT_URL = API_GATEWAY_URL;
// Orchestration now accessed through API Gateway proxy
export const COMPUTE_URL = `${API_GATEWAY_URL}/v1`;
// Inference Gateway - separate public service
export const INFERENCE_URL = rc.INFERENCE_URL || import.meta.env.VITE_INFERENCE_URL || "http://localhost:8001";
// WebSocket still goes through sidecar for DePIN
export const WEB_SOCKET_URL = rc.WEB_SOCKET_URL || import.meta.env.VITE_WEB_SOCKET_URL || "ws://localhost:3000";

/** Build an absolute ws(s):// URL for a gateway path. API_GATEWAY_URL may be a
 *  relative same-origin value ("/api") or absolute ("http://host:8000"); resolve
 *  against the page origin and pick ws/wss from the resolved scheme. */
export function toWsUrl(path: string): string {
    const u = new URL(API_GATEWAY_URL || "/", window.location.origin);
    const proto = u.protocol === "https:" ? "wss:" : "ws:";
    const base = u.pathname.replace(/\/$/, "");
    return `${proto}//${u.host}${base}${path}`;
}

// ── Silent-refresh state (shared across all axios instances) ────────
let isRefreshing = false;
let refreshSubscribers: Array<(token: string) => void> = [];

function onTokenRefreshed(token: string) {
    refreshSubscribers.forEach((cb) => cb(token));
    refreshSubscribers = [];
}

function addRefreshSubscriber(cb: (token: string) => void) {
    refreshSubscribers.push(cb);
}

function forceLogout() {
    clearToken();
    // In external-auth mode there is no local refresh token — a 401 means the
    // short-lived IdP access token expired. Send the browser to /auth/start so
    // the IdP's SSO cookie silently re-issues a new token without showing a
    // login page. Guard against redirect loops on any /auth/* route.
    if (isExternalAuthMode()) {
        if (!window.location.pathname.startsWith("/auth/")) {
            window.location.href = "/auth/start";
        }
        return;
    }
    if (window.location.pathname !== "/auth/login") {
        window.location.href = "/auth/login";
    }
}

// Client Factory
const createApiClient = (baseURL: string): AxiosInstance => {
    const instance = axios.create({
        baseURL,
        headers: {
            "Content-Type": "application/json",
        },
    });

    // Request Interceptor: Attach Token (from in-memory store, not localStorage)
    instance.interceptors.request.use(
        (config) => {
            const token = getToken();
            if (token) {
                config.headers.Authorization = `Bearer ${token}`;
            }
            return config;
        },
        (error) => Promise.reject(error)
    );

    // Response Interceptor: Handle 401 with silent refresh
    instance.interceptors.response.use(
        (response) => response,
        async (error) => {
            const status = error.response?.status;
            const detail = error.response?.data?.detail;
            const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean };

            if (status === 401 && !originalRequest._retry) {
                // Don't try to refresh if we're already on the login page or
                // if the failing request was itself a refresh call.
                const url = originalRequest.url || "";
                if (window.location.pathname === "/auth/login" || url.includes("/auth/refresh")) {
                    return Promise.reject(error);
                }

                const rt = getRefreshToken();
                if (!rt) {
                    forceLogout();
                    return Promise.reject(error);
                }

                originalRequest._retry = true;

                if (!isRefreshing) {
                    isRefreshing = true;
                    try {
                        const { data } = await axios.post(
                            `${API_GATEWAY_URL}/auth/refresh`,
                            null,
                            { headers: { Authorization: `Bearer ${rt}` } },
                        );
                        setToken(data.access_token);
                        setRefreshToken(data.refresh_token);
                        isRefreshing = false;
                        onTokenRefreshed(data.access_token);
                        // Retry the original request with the new token
                        originalRequest.headers.Authorization = `Bearer ${data.access_token}`;
                        return instance(originalRequest);
                    } catch {
                        isRefreshing = false;
                        refreshSubscribers = [];
                        forceLogout();
                        return Promise.reject(error);
                    }
                }

                // Another request already triggered a refresh — queue this one
                return new Promise((resolve) => {
                    addRefreshSubscriber((newToken: string) => {
                        originalRequest.headers.Authorization = `Bearer ${newToken}`;
                        resolve(instance(originalRequest));
                    });
                });
            } else if (status === 403) {
                toast.error(detail || "You don't have permission for this action.");
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
export const computeApi = createApiClient(`${API_GATEWAY_URL}/v1`);

// Deployment API - uses gateway proxy routes to orchestration service
export const deploymentApi = createApiClient(`${API_GATEWAY_URL}/v1/deployments`);
export const poolApi = createApiClient(`${API_GATEWAY_URL}/v1/pools`);
export const insightApi = createApiClient(`${API_GATEWAY_URL}/v1/insights`);

// Default export alias for backward compatibility
export default api;
