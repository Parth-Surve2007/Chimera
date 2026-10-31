from chimera.interaction import InteractionBackendInfo, InteractionStrategyEngine, UnavailableVisualProvider
from chimera.providers import ProviderCapabilities, ProviderRegistration, ProviderRegistry

def test_strategy_prefers_semantic_backend():
    engine = InteractionStrategyEngine([
        type("B", (), {"describe": lambda self: InteractionBackendInfo("grounded_input", True, ("focus",), (), ("native_windows",))})(),
        type("B", (), {"describe": lambda self: InteractionBackendInfo("uia", True, ("focus",), (), ("native_windows",))})(),
    ])
    assert engine.choose("focus", "native_windows", evidence={"semantic_control": True}).selected == "uia"

def test_strategy_does_not_choose_coordinates_without_evidence():
    engine = InteractionStrategyEngine([type("B", (), {"describe": lambda self: InteractionBackendInfo("grounded_input", True, ("focus",), (), ("native_windows",))})()])
    assert engine.choose("focus").selected is None

def test_visual_provider_is_honest_when_unconfigured():
    assert UnavailableVisualProvider().inspect("x.png")["status"] == "unavailable"

def test_provider_registry_checks_capabilities():
    registry = ProviderRegistry()
    registry.register(ProviderRegistration(ProviderCapabilities("local", "model", tool_calling=True), lambda: "provider"))
    assert registry.select("local", require_tools=True) == "provider"
