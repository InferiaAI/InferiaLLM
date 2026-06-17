import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { NosanaMetadataGrid } from "./NosanaMetadataGrid";
import type { DepinDetails } from "@/services/nodeService";

const full: DepinDetails = {
  provider: "nosana",
  job_address: "JOBaddr",
  node_address: "NODEaddr",
  deployment_address: "DEPLaddr",
  run_address: "RUNaddr",
  market: "MKT",
  service_url: "https://x.node.k8s.prd.nos.ci",
  image: "vllm/vllm-openai:v0.16.0",
  mode: "real",
  tx: "TXsig",
  provider_credential_name: "cred1",
  gpu_total: 1,
  price: "0",
  job_state: "RUNNING",
  created_at: "2026-06-17T09:36:19Z",
};

describe("NosanaMetadataGrid", () => {
  it("renders the Nosana instance fields", () => {
    render(<NosanaMetadataGrid details={full} />);
    expect(screen.getByText("Job address")).toBeInTheDocument();
    expect(screen.getByText("JOBaddr")).toBeInTheDocument();
    expect(screen.getByText("NODEaddr")).toBeInTheDocument();
    expect(screen.getByText("DEPLaddr")).toBeInTheDocument();
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(screen.getByText("vllm/vllm-openai:v0.16.0")).toBeInTheDocument();
    // labels present
    expect(screen.getByText("Service URL")).toBeInTheDocument();
    expect(screen.getByText("Job state")).toBeInTheDocument();
  });

  it("renders an em dash for null/empty values", () => {
    const sparse: DepinDetails = {
      provider: "nosana",
      job_address: "JOBaddr",
      node_address: null,
      deployment_address: null,
      run_address: null,
      market: null,
      service_url: null,
      image: null,
      mode: null,
      tx: null,
      provider_credential_name: null,
      gpu_total: null,
      price: null,
      job_state: null,
      created_at: null,
    };
    render(<NosanaMetadataGrid details={sparse} />);
    // every null field collapses to the em dash placeholder
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    // the one present value still shows
    expect(screen.getByText("JOBaddr")).toBeInTheDocument();
  });
});
