from jobhunt.ia import validar as v


def test_texto_suficiente():
    assert not v.texto_suficiente("")
    assert not v.texto_suficiente("x" * 199)
    assert v.texto_suficiente("x" * 200)


def test_techs_solo_si_figuran_en_texto():
    txt = "Buscamos dev con Java, Spring Boot y TypeScript. Deseable Docker."
    alias = {"ts": ("typescript",)}
    out = v.limpiar_techs(["Java", "Python", "TS", "Spring Boot", "integración de siste", "Docker"], txt, alias)
    assert out == ["Java", "TS", "Spring Boot", "Docker"]


def test_techs_frases_largas_fuera():
    assert v.limpiar_techs(["desarrollo backend de aplicaciones"], "desarrollo backend de aplicaciones", {}) == []


def test_flags_sin_ruido_ni_duplicados():
    assert v.limpiar_flags(["no declarado", "No declarado", "Bono anual", "Bono anual"]) == ["Bono anual"]
    assert v.quitar_duplicados(["seguro", "gym"], ["Seguro"]) == ["gym"]


def test_opinion_que_proyecta_stack_del_perfil():
    perfil = ["python", "java", "spring boot"]
    oferta = "Analista de datos con Power BI y SQL"
    assert v.texto_cita_perfil_ajeno("El stack Python/Java coincide con el perfil", oferta, perfil)
    assert not v.texto_cita_perfil_ajeno("El stack Python/Java no coincide con el perfil", oferta, perfil)
    assert not v.texto_cita_perfil_ajeno("Usa Power BI", oferta, perfil)
    assert not v.texto_cita_perfil_ajeno("Pide Python", "Se requiere Python y SQL", perfil)


def test_ingles_desde_texto():
    assert v.ingles_desde_texto("Inglés avanzado (C1+) | Inglés–Español") == "requerido"
    assert v.ingles_desde_texto("Inglés deseable") == "deseable"
    assert v.ingles_desde_texto("Buscamos programadores") == ""


def test_techs_sin_conectores_de_frase():
    txt = "integración de sistemas y simuladores de vuelo con Power BI"
    assert v.limpiar_techs(["integración de sistemas", "simuladores de vuelo", "Power BI"], txt, {}) == ["Power BI"]
