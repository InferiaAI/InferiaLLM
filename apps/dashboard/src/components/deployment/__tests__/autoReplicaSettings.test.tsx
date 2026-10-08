import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { AutoReplicaSettings } from "../DeploymentConfig";

const KEDA_LABELS = [
  /p95 latency target/i,
  /max replicas/i,
  /in-flight per replica/i,
  /minimum samples/i,
];

function renderSettings(over: Record<string, unknown> = {}) {
  const state = {
    autoReplicaEnabled: true,
    tokensPerSecondThreshold: "10",
    kedaP95Seconds: "",
    kedaMaxReplicas: "",
    kedaInFlightPerReplica: "",
    kedaMinSamples: "",
    ...over,
  };
  return render(
    <AutoReplicaSettings
      autoReplicaEnabled={state.autoReplicaEnabled as boolean}
      tokensPerSecondThreshold={state.tokensPerSecondThreshold as string}
      isK8s={(over.isK8s as boolean) ?? false}
      state={state as never}
      dispatch={vi.fn()}
    />
  );
}

describe("the two autoscalers are never offered together", () => {
  it("a kubernetes pool gets the KEDA targets and no tokens/sec field", () => {
    renderSettings({ isK8s: true });

    for (const label of KEDA_LABELS) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    // The TPS autoscaler skips k8s deployments, so the field would do nothing.
    expect(screen.queryByLabelText(/tokens\/sec threshold/i)).toBeNull();
  });

  it("any other pool gets the tokens/sec field and no KEDA targets", () => {
    renderSettings({ isK8s: false });

    expect(screen.getByLabelText(/tokens\/sec threshold/i)).toBeInTheDocument();
    for (const label of KEDA_LABELS) {
      expect(screen.queryByLabelText(label)).toBeNull();
    }
  });
});

describe("when auto-replica is off", () => {
  it("neither set of fields is offered", () => {
    renderSettings({ autoReplicaEnabled: false, isK8s: true });

    expect(screen.queryByLabelText(/tokens\/sec threshold/i)).toBeNull();
    for (const label of KEDA_LABELS) {
      expect(screen.queryByLabelText(label)).toBeNull();
    }
  });
});

describe("the platform defaults", () => {
  it("are labelled as defaults, so they cannot be read as set values", () => {
    renderSettings({ isK8s: true });

    expect(screen.getByLabelText(/p95 latency target/i)).toHaveAttribute("placeholder", "default 10");
    expect(screen.getByLabelText(/max replicas/i)).toHaveAttribute("placeholder", "default 3");
    expect(screen.getByLabelText(/in-flight per replica/i)).toHaveAttribute("placeholder", "default 3");
    expect(screen.getByLabelText(/minimum samples/i)).toHaveAttribute("placeholder", "default 2");
  });

  it("a stored value is shown instead of the placeholder", () => {
    renderSettings({ isK8s: true, kedaP95Seconds: "2" });

    expect(screen.getByLabelText(/p95 latency target/i)).toHaveValue(2);
  });
});

describe("the validation bounds match the API", () => {
  it("each field carries the range the backend enforces", () => {
    renderSettings({ isK8s: true });

    const bounds: [RegExp, string, string][] = [
      [/p95 latency target/i, "1", "300"],
      [/max replicas/i, "1", "50"],
      [/in-flight per replica/i, "1", "100"],
      [/minimum samples/i, "1", "1000"],
    ];
    for (const [label, min, max] of bounds) {
      const input = screen.getByLabelText(label);
      expect(input).toHaveAttribute("min", min);
      expect(input).toHaveAttribute("max", max);
    }
  });
});
