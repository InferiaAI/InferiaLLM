import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/context/AuthContext";
import { api } from "@/lib/api";

// The page contract this dashboard implements: a page module exports
// mount(element, { api, version }) and returns a function that unmounts it.
export const EXTENSION_PAGE_CONTRACT = 1;

export type ExtensionSection = "monitor" | "build" | "admin";

export interface ExtensionPage {
  plugin: string;
  id: string;
  title: string;
  icon: string;
  section: ExtensionSection;
  contract: number;
  script: string; // gateway path of the page's module
}

export const extensionPagePath = (page: Pick<ExtensionPage, "plugin" | "id">) =>
  `/dashboard/ext/${page.plugin}/${page.id}`;

/**
 * Pages added by installed extensions, already filtered to those the user may
 * open. Empty when no extension is installed: the listing then does not exist.
 * Any other failure is an error, retried, so a brief outage does not hide menus.
 */
export function useExtensionPages() {
  const { user } = useAuth();
  const query = useQuery({
    queryKey: ["extension-pages", user?.org_id],
    queryFn: async () => {
      try {
        const { data } = await api.get<{ pages: ExtensionPage[] }>("/ext/_pages");
        return data.pages.filter((p) => p.contract === EXTENSION_PAGE_CONTRACT);
      } catch (err) {
        if ((err as { response?: { status?: number } })?.response?.status === 404) return [];
        throw err;
      }
    },
    enabled: Boolean(user),
    staleTime: 5 * 60_000,
  });
  return { pages: query.data ?? [], isLoading: query.isLoading, isError: query.isError };
}
