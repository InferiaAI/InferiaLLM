import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Puzzle } from "lucide-react";
import { api } from "@/lib/api";
import { EXTENSION_PAGE_CONTRACT, useExtensionPages } from "@/hooks/useExtensionPages";

type Mount = (element: HTMLElement, ctx: { api: typeof api; version: number }) => () => void;

export default function ExtensionPage() {
  const { plugin, page: pageId } = useParams();
  const { pages, isLoading, isError } = useExtensionPages();
  const page = pages.find((p) => p.plugin === plugin && p.id === pageId);
  const host = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!page || !host.current) return;
    const element = host.current;
    let cancelled = false;
    let unmount: (() => void) | undefined;
    setFailed(false);

    (async () => {
      let url: string | undefined;
      try {
        // The script sits behind the gateway's sign-in, which takes only a Bearer
        // header, so it is fetched with the signed-in client and imported from a
        // Blob URL; a plain import() of its address would get a 401.
        const { data } = await api.get<string>(page.script, { responseType: "text", transformResponse: (d) => d });
        url = URL.createObjectURL(new Blob([data], { type: "text/javascript" }));
        const mod: { mount?: Mount } = await import(/* @vite-ignore */ url);
        if (cancelled) return;
        if (typeof mod.mount !== "function") throw new Error("no mount export");
        unmount = mod.mount(element, { api, version: EXTENSION_PAGE_CONTRACT });
      } catch {
        if (!cancelled) setFailed(true);
      } finally {
        if (url) URL.revokeObjectURL(url);
      }
    })();

    return () => {
      cancelled = true;
      unmount?.();
    };
  }, [page?.script]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!page) {
    if (isLoading) return null;
    if (isError) return <Notice title="This page could not be loaded" text="The list of installed pages is unavailable. Try again shortly." />;
    return <Notice title="Page not found" text="This page is not installed, or you do not have access to it." />;
  }
  return (
    <>
      {failed && <Notice title={`${page.title} could not be loaded`} text="Try again, or ask an administrator to check the extension." />}
      <div ref={host} />
    </>
  );
}

function Notice({ title, text }: { title: string; text: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border bg-card px-4 py-16 text-center">
      <Puzzle className="mb-3 h-8 w-8 text-muted-foreground opacity-50" />
      <p className="font-medium">{title}</p>
      <p className="mt-1 text-sm text-muted-foreground">{text}</p>
    </div>
  );
}
