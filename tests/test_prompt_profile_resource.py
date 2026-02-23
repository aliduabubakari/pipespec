from pipespec_validator import load_prompt_profile


def test_prompt_profile_loads():
    prof = load_prompt_profile()
    assert isinstance(prof, dict)
    assert prof.get("x_non_normative") is True