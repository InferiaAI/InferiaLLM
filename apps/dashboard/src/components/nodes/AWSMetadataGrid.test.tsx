import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AWSMetadataGrid } from "./AWSMetadataGrid";


describe("AWSMetadataGrid", () => {
  it("renders all six fields", () => {
    render(
      <AWSMetadataGrid
        metadata={{
          instance_class: "normal_gpu",
          instance_type:  "g6.xlarge",
          region:         "us-east-1",
          ami_id:         "ami-deadbeef",
          instance_id:    "i-0abc1234",
          public_dns:     "ec2-1-2-3-4.compute-1.amazonaws.com",
        }}
      />
    );
    expect(screen.getByText("g6.xlarge")).toBeInTheDocument();
    expect(screen.getByText("us-east-1")).toBeInTheDocument();
    expect(screen.getByText("ami-deadbeef")).toBeInTheDocument();
    expect(screen.getByText("i-0abc1234")).toBeInTheDocument();
    expect(screen.getByText(/ec2-1-2-3-4/)).toBeInTheDocument();
  });

  it("renders em-dash placeholders for null fields", () => {
    render(
      <AWSMetadataGrid
        metadata={{
          instance_class: "normal_gpu",
          instance_type:  "g6.xlarge",
          region:         "us-east-1",
          ami_id:         "ami-x",
          instance_id:    null,
          public_dns:     null,
        }}
      />
    );
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("renders 'Normal GPU' label for normal_gpu class", () => {
    render(
      <AWSMetadataGrid
        metadata={{
          instance_class: "normal_gpu",
          instance_type:  "g6.xlarge",
          region:         "us-east-1",
          ami_id:         "ami-x",
          instance_id:    null,
          public_dns:     null,
        }}
      />
    );
    expect(screen.getByText("Normal GPU")).toBeInTheDocument();
  });
});
