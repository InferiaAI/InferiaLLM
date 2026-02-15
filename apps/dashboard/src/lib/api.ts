import axios, { type AxiosInstance } from "axios";

// URLs
export const MANAGEMENT_URL = import.meta.env.VITE_MANAGEMENT_URL || "http://localhost:8000";
export const COMPUTE_URL = import.meta.env.VITE_COMPUTE_URL || "http://localhost:8080";
export const WEB_SOCKET_URL = import.meta.env.VITE_WEB_SOCKET_URL || "ws://localhost:3000";
export const INFERENCE_URL = import.meta.env.VITE_INFERENCE_URL || "http://localhost:8001";
export const SIDECAR_URL = import.meta.env.VITE_SIDECAR_URL || "http://localhost:3000";
export const DATA_URL = import.meta.env.VITE_DATA_URL || "http://localhost:8003";
export const GUARDRAIL_URL = import.meta.env.VITE_GUARDRAIL_URL || "http://localhost:8002";

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

export const managementApi = createApiClient(MANAGEMENT_URL);
export const computeApi = createApiClient(COMPUTE_URL);

// Default export alias for backward compatibility (points to Management API)
export default managementApi;
