import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            retry: 1, // Fail faster on dev
            refetchOnWindowFocus: false, // Prevent too many reloads during dev toggling
            staleTime: 1000 * 60 * 5, // Data is fresh for 5 minutes by default
        },
        mutations: {
            retry: 0,
        },
    },
});
