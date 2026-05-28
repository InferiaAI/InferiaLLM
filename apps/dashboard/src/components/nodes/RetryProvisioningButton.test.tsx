import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { RetryProvisioningButton } from "./RetryProvisioningButton";


function _wrap(ui: React.ReactElement) {
  const qc = new QueryClient();
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}


describe("RetryProvisioningButton", () => {
  it("posts to /retry on click", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ job_id: "j-1" }), { status: 200 }),
    );
    render(_wrap(<RetryProvisioningButton nodeId="node-1" />));
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/nodes/node-1/provisioning/retry"),
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("is disabled while the retry is in-flight", async () => {
    vi.spyOn(global, "fetch").mockImplementation(
      () => new Promise(() => {}),  // never resolves
    );
    render(_wrap(<RetryProvisioningButton nodeId="node-1" />));
    const btn = screen.getByRole("button", { name: /retry/i });
    fireEvent.click(btn);
    await waitFor(() => expect(btn).toBeDisabled());
  });

  it("calls onSuccess after successful retry", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ job_id: "j-1" }), { status: 200 }),
    );
    const onSuccess = vi.fn();
    render(_wrap(<RetryProvisioningButton nodeId="node-1" onSuccess={onSuccess} />));
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
  });
});
