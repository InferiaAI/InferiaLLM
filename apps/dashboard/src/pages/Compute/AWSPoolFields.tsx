/* eslint-disable react-refresh/only-export-components */
/**
 * Shared AWS provisioning configuration fields.
 *
 * Used by both NewPool (pool creation) and the AWS Config tab in
 * InstanceDetail (pool metadata editing).
 *
 * This file intentionally exports both utility functions and a component;
 * fast-refresh still works in practice because the component is the default
 * re-render target and the utilities are pure functions / constants.
 */

import React from "react";

// ---------------------------------------------------------------------------
// Validation helpers (mirrors backend Pydantic validators in AWSPoolMetadata)
// ---------------------------------------------------------------------------

export const AWS_REGEX = {
    subnet: /^subnet-[0-9a-f]{8,17}$/,
    sg: /^sg-[0-9a-f]{8,17}$/,
    ami: /^ami-[0-9a-f]{8,17}$/,
    iamProfile: /^arn:aws:iam::\d{12}:instance-profile\/.+$/,
};

export type AWSMeta = {
    subnet_id: string;
    security_group_ids: string[];
    ami_id?: string;
    iam_instance_profile?: string;
    root_volume_gb: number;
    worker_image_tag?: string;
};

export const DEFAULT_AWS_META: AWSMeta = {
    subnet_id: "",
    security_group_ids: [],
    ami_id: "",
    iam_instance_profile: "",
    root_volume_gb: 100,
    worker_image_tag: "",
};

export function validateAwsMeta(m: AWSMeta): Record<string, string> {
    const errs: Record<string, string> = {};
    if (!m.subnet_id) errs.subnet_id = "Subnet ID is required";
    else if (!AWS_REGEX.subnet.test(m.subnet_id)) errs.subnet_id = "Must match subnet-XXXXXXXX";
    if (!m.security_group_ids || m.security_group_ids.length === 0) {
        errs.security_group_ids = "At least one security group is required";
    } else {
        for (const sg of m.security_group_ids) {
            if (!AWS_REGEX.sg.test(sg)) { errs.security_group_ids = `Invalid: ${sg}`; break; }
        }
    }
    if (m.ami_id && !AWS_REGEX.ami.test(m.ami_id)) errs.ami_id = "Must match ami-XXXXXXXX";
    if (m.iam_instance_profile && !AWS_REGEX.iamProfile.test(m.iam_instance_profile)) {
        errs.iam_instance_profile = "Must match arn:aws:iam::ACCOUNT:instance-profile/NAME";
    }
    if (m.root_volume_gb && (m.root_volume_gb < 10 || m.root_volume_gb > 16384)) {
        errs.root_volume_gb = "Must be 10..16384";
    }
    if (m.worker_image_tag && /\s/.test(m.worker_image_tag)) {
        errs.worker_image_tag = "No whitespace allowed";
    }
    return errs;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type Props = {
    value: AWSMeta;
    onChange: (next: AWSMeta) => void;
    errors: Record<string, string>;
};

export function AWSPoolFields({ value, onChange, errors }: Props) {
    return (
        <div className="space-y-4 rounded-lg border border-zinc-800 bg-zinc-950/40 p-4">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold">AWS provisioning configuration</h3>
                <span className="text-xs text-zinc-500">Required for EC2 spin-up</span>
            </div>

            {/* subnet_id */}
            <div className="space-y-2">
                <label htmlFor="aws-subnet" className="text-sm font-medium">
                    Subnet ID <span className="text-red-400">*</span>
                </label>
                <input
                    id="aws-subnet"
                    type="text"
                    placeholder="subnet-0123456789abcdef0"
                    value={value.subnet_id || ""}
                    onChange={(e) => onChange({ ...value, subnet_id: e.target.value })}
                    className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm"
                />
                {errors.subnet_id && <p className="text-xs text-red-400">{errors.subnet_id}</p>}
            </div>

            {/* security_group_ids — comma-separated */}
            <div className="space-y-2">
                <label htmlFor="aws-sg" className="text-sm font-medium">
                    Security Group IDs <span className="text-red-400">*</span>
                </label>
                <input
                    id="aws-sg"
                    type="text"
                    placeholder="sg-abc12345, sg-def67890"
                    value={(value.security_group_ids || []).join(", ")}
                    onChange={(e) =>
                        onChange({
                            ...value,
                            security_group_ids: e.target.value
                                .split(",")
                                .map((s) => s.trim())
                                .filter(Boolean),
                        })
                    }
                    className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm"
                />
                <p className="text-xs text-zinc-500">Comma-separated; at least one required.</p>
                {errors.security_group_ids && (
                    <p className="text-xs text-red-400">{errors.security_group_ids}</p>
                )}
            </div>

            {/* ami_id (optional) */}
            <div className="space-y-2">
                <label htmlFor="aws-ami" className="text-sm font-medium">
                    AMI ID <span className="text-zinc-500">(optional)</span>
                </label>
                <input
                    id="aws-ami"
                    type="text"
                    placeholder="ami-deadbeef00000000 (auto-detect DLAMI if blank)"
                    value={value.ami_id || ""}
                    onChange={(e) => onChange({ ...value, ami_id: e.target.value })}
                    className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm"
                />
                {errors.ami_id && <p className="text-xs text-red-400">{errors.ami_id}</p>}
            </div>

            {/* iam_instance_profile (optional) */}
            <div className="space-y-2">
                <label htmlFor="aws-iam" className="text-sm font-medium">
                    IAM instance profile ARN <span className="text-zinc-500">(optional)</span>
                </label>
                <input
                    id="aws-iam"
                    type="text"
                    placeholder="arn:aws:iam::123456789012:instance-profile/inferia-worker"
                    value={value.iam_instance_profile || ""}
                    onChange={(e) => onChange({ ...value, iam_instance_profile: e.target.value })}
                    className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm"
                />
                {errors.iam_instance_profile && (
                    <p className="text-xs text-red-400">{errors.iam_instance_profile}</p>
                )}
            </div>

            {/* root_volume_gb */}
            <div className="space-y-2">
                <label htmlFor="aws-root-gb" className="text-sm font-medium">
                    Root EBS volume (GB) <span className="text-zinc-500">(default 100)</span>
                </label>
                <input
                    id="aws-root-gb"
                    type="number"
                    min={10}
                    max={16384}
                    value={value.root_volume_gb ?? 100}
                    onChange={(e) =>
                        onChange({ ...value, root_volume_gb: parseInt(e.target.value, 10) || 100 })
                    }
                    className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm"
                />
                {errors.root_volume_gb && (
                    <p className="text-xs text-red-400">{errors.root_volume_gb}</p>
                )}
            </div>

            {/* worker_image_tag */}
            <div className="space-y-2">
                <label htmlFor="aws-image-tag" className="text-sm font-medium">
                    inferia-worker image tag <span className="text-zinc-500">(default "latest")</span>
                </label>
                <input
                    id="aws-image-tag"
                    type="text"
                    placeholder="v1.2.3"
                    value={value.worker_image_tag || ""}
                    onChange={(e) => onChange({ ...value, worker_image_tag: e.target.value })}
                    className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm"
                />
                {errors.worker_image_tag && (
                    <p className="text-xs text-red-400">{errors.worker_image_tag}</p>
                )}
            </div>
        </div>
    );
}
