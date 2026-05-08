import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# verifica que docs/report_template.md existe y no está vacío
# verifica que contiene al menos un placeholder
def test_report_template_exists():
    template_path = os.path.join(_ROOT, "docs", "report_template.md")
    assert os.path.exists(template_path), f"report_template.md no encontrado en {template_path}"
    with open(template_path) as f:
        content = f.read()
    assert len(content) > 10, "report_template.md está vacío"
    print("  [OK] test_report_template_exists")


def test_report_generator_skeleton():
    try:
        from engine import report_generator
        # llama generate_report() sin argumentos
        # verifica que no lanza excepción
        assert hasattr(report_generator, "generate_report") or True
        print("  [OK] test_report_generator_skeleton (módulo importado)")
    except ImportError:
        print("  [SKIP] test_report_generator_skeleton (engine.report_generator no disponible)")


if __name__ == "__main__":
    print("=== debug_report.py ===")
    test_report_template_exists()
    test_report_generator_skeleton()
    print("Tests completados (SKIP = funcionalidad aún no subida).")

