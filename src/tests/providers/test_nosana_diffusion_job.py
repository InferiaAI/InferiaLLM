from providers.nosana.job_builder import create_inferia_diffusion_job, build_job_definition


def test_diffusion_job_emits_model_type_and_flags():
    job = create_inferia_diffusion_job(
        model_id="stabilityai/sdxl-turbo",
        model_type="image_generation",
        trust_remote_code=True,
        model_offload=True,
        group_offload=False,
    )
    cmd = job["op"]["args"]["cmd"]
    assert cmd[:2] == ["inferiadiffusion", "serve"]
    assert "--model" in cmd and "stabilityai/sdxl-turbo" in cmd
    i = cmd.index("--model-type")
    assert cmd[i + 1] == "image"
    assert "--trust-remote-code" in cmd
    assert "--model-offload" in cmd
    assert "--group-offload" not in cmd


def test_diffusion_video_type_maps_to_video():
    job = create_inferia_diffusion_job(model_id="Wan-AI/Wan2.1-T2V-1.3B", model_type="video_generation")
    cmd = job["op"]["args"]["cmd"]
    i = cmd.index("--model-type")
    assert cmd[i + 1] == "video"


def test_diffusion_no_model_type_omits_flag():
    job = create_inferia_diffusion_job(model_id="segmind/tiny-sd")
    assert "--model-type" not in job["op"]["args"]["cmd"]


def test_diffusion_unknown_model_type_omits_flag():
    job = create_inferia_diffusion_job(model_id="segmind/tiny-sd", model_type="audio")
    assert "--model-type" not in job["op"]["args"]["cmd"]


def test_build_job_definition_threads_diffusion_flags():
    full = build_job_definition(
        engine="inferia-diffusion",
        model_id="segmind/tiny-sd",
        model_type="image_generation",
        trust_remote_code=True,
    )
    cmd = full["ops"][0]["args"]["cmd"]
    assert "--model-type" in cmd and "--trust-remote-code" in cmd


from providers.nosana.nosana_adapter import _diffusion_job_overrides


def test_diffusion_overrides_reads_nested_config():
    md = {"config": {"model_type": "video_generation", "trust_remote_code": True,
                     "model_offload": True, "group_offload": False}}
    out = _diffusion_job_overrides(md)
    assert out["model_type"] == "video_generation"
    assert out["trust_remote_code"] is True
    assert out["model_offload"] is True
    assert out["group_offload"] is False


def test_diffusion_overrides_missing_config_defaults_false():
    out = _diffusion_job_overrides({})
    assert out["model_type"] is None
    assert out["trust_remote_code"] is False
    assert out["model_offload"] is False
    assert out["group_offload"] is False


def test_diffusion_overrides_non_dict_config_is_safe():
    out = _diffusion_job_overrides({"config": "not-a-dict"})
    assert out["model_type"] is None
    assert out["trust_remote_code"] is False
