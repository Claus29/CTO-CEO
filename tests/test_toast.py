"""Avisos nativos de Windows: XML válido (con textos con símbolos), enlaces seguros y guion de PowerShell."""
import base64
import xml.dom.minidom

from devcontrol import toast


def test_toast_xml_is_valid_and_escaped():
    x = toast.toast_xml('T-003 · <Login> & "comillas"', "Luis cambió la prioridad\nAlta", "devcontrol://aviso/5", urgent=True)
    doc = xml.dom.minidom.parseString(x)
    texts = [n.firstChild.data for n in doc.getElementsByTagName("text")]
    assert texts[0] == 'T-003 · <Login> & "comillas"'
    assert doc.documentElement.getAttribute("launch") == "devcontrol://aviso/5"
    assert doc.getElementsByTagName("action")            # lo urgente se queda en pantalla con botón «Abrir»
    assert not xml.dom.minidom.parseString(toast.toast_xml("a", "b", "devcontrol://aviso/1")).getElementsByTagName("action")


def test_only_notice_links_are_accepted():
    assert toast.parse_link("devcontrol://aviso/123") == "aviso/123"
    assert toast.parse_link("DEVCONTROL://aviso/9/") == "aviso/9"
    for bad in ("devcontrol://aviso/x", "devcontrol://../../etc", "http://aviso/1", "", "devcontrol://aviso/" + "9" * 20):
        assert toast.parse_link(bad) == ""


def test_powershell_script_embeds_the_toast_safely():
    xml_text = toast.toast_xml("Título con 'comillas'", "texto", "devcontrol://aviso/2")
    script = toast.powershell_script(xml_text, "tag'; borrar", dry=True)
    encoded = script.split("FromBase64String('")[1].split("'")[0]
    assert base64.b64decode(encoded).decode() == xml_text      # el texto va codificado: nada se interpreta
    assert "borrar" in script and "';" not in script.split("$t.Tag=")[1].split(";")[0][1:-1]
    assert ".Show(" not in script and toast.AUMID in script
    assert toast.show_toast("a", "b", "devcontrol://aviso/1") is False or __import__("os").name == "nt"
