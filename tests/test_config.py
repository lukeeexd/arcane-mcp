import pytest

from arcane_mcp.config import DEFAULT_TAGS, ConfigError, load_settings

REQUIRED = {
    "ARCANE_BASE_URL": "https://arcane.example.com/",
    "ARCANE_API_KEY": "arc_key",
    "MCP_AUTH_TOKEN": "secret",
}


def test_defaults():
    s = load_settings(REQUIRED)
    assert s.arcane_base_url == "https://arcane.example.com"
    assert s.arcane_api_key == "arc_key"
    assert s.mcp_auth_token == "secret"
    assert s.mcp_host == "0.0.0.0"
    assert s.mcp_port == 8000
    assert s.tags == DEFAULT_TAGS
    assert "Containers" in DEFAULT_TAGS and "Users" not in DEFAULT_TAGS
    assert s.exclude_tags == frozenset()
    assert s.read_only is False
    assert s.confirm_destructive is True
    assert s.destructive_patterns == ()
    assert s.spec_timeout == 60.0
    assert s.log_level == "INFO"


def test_strips_trailing_api_segment():
    s = load_settings({**REQUIRED, "ARCANE_BASE_URL": "http://h:3552/api/"})
    assert s.arcane_base_url == "http://h:3552"


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_names_variable(missing):
    env = {k: v for k, v in REQUIRED.items() if k != missing}
    with pytest.raises(ConfigError) as exc:
        load_settings(env)
    assert missing in str(exc.value)


def test_blank_required_is_missing():
    with pytest.raises(ConfigError):
        load_settings({**REQUIRED, "ARCANE_API_KEY": "  "})


def test_list_and_bool_parsing():
    s = load_settings({
        **REQUIRED,
        "ARCANE_MCP_TAGS": " Containers, Swarm ,,Images ",
        "ARCANE_MCP_EXCLUDE_TAGS": "Images",
        "ARCANE_MCP_READ_ONLY": "TRUE",
        "ARCANE_MCP_CONFIRM_DESTRUCTIVE": "0",
        "ARCANE_MCP_DESTRUCTIVE_PATTERNS": r"/reset$, /wipe",
        "ARCANE_MCP_SPEC_TIMEOUT": "5",
        "MCP_PORT": "9000",
        "MCP_HOST": "127.0.0.1",
        "ARCANE_MCP_LOG_LEVEL": "debug",
    })
    assert s.tags == frozenset({"Containers", "Swarm", "Images"})
    assert s.exclude_tags == frozenset({"Images"})
    assert s.read_only is True
    assert s.confirm_destructive is False
    assert s.destructive_patterns == (r"/reset$", "/wipe")
    assert s.spec_timeout == 5.0
    assert s.mcp_port == 9000
    assert s.mcp_host == "127.0.0.1"
    assert s.log_level == "DEBUG"


@pytest.mark.parametrize("value", ["yes", "true", "1", "on", "True"])
def test_truthy_values(value):
    assert load_settings({**REQUIRED, "ARCANE_MCP_READ_ONLY": value}).read_only is True


def test_bad_bool_raises():
    with pytest.raises(ConfigError):
        load_settings({**REQUIRED, "ARCANE_MCP_READ_ONLY": "maybe"})


def test_bad_port_raises():
    with pytest.raises(ConfigError):
        load_settings({**REQUIRED, "MCP_PORT": "abc"})
