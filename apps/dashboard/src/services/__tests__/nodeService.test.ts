import { describe, it, expect, vi, beforeEach } from "vitest";
import { deleteNode } from "../nodeService";

const deleteMock = vi.fn();
vi.mock("@/lib/api", () => ({
  computeApi: {
    delete: (path: string) => deleteMock(path),
  },
}));

describe("nodeService.deleteNode", () => {
  beforeEach(() => deleteMock.mockReset());

  it("returns terminating=true for 202 response with body (AWS path)", async () => {
    deleteMock.mockResolvedValueOnce({
      status: 202,
      data: { node_id: "n1", state: "terminating" },
    });
    const result = await deleteNode("n1");
    expect(result.terminating).toBe(true);
    expect(result.state).toBe("terminating");
    expect(result.nodeId).toBe("n1");
    expect(deleteMock).toHaveBeenCalledWith("/nodes/n1");
  });

  it("returns terminating=false for 204 No Content (non-AWS path)", async () => {
    // axios returns status:204 with data="" for No Content responses.
    deleteMock.mockResolvedValueOnce({
      status: 204,
      data: "",
    });
    const result = await deleteNode("n2");
    expect(result.terminating).toBe(false);
    expect(result.state).toBeUndefined();
    expect(deleteMock).toHaveBeenCalledWith("/nodes/n2");
  });

  it("does not throw on empty body for 204", async () => {
    deleteMock.mockResolvedValueOnce({ status: 204, data: undefined });
    await expect(deleteNode("n3")).resolves.toEqual({ terminating: false });
  });

  it("propagates rejection (e.g. 404) to caller", async () => {
    deleteMock.mockRejectedValueOnce(
      Object.assign(new Error("not found"), {
        response: { status: 404, data: { detail: "node not found" } },
      }),
    );
    await expect(deleteNode("missing")).rejects.toThrow("not found");
  });
});
