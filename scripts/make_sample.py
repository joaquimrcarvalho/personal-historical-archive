"""Generate a small sample 'manuscript' for end-to-end testing.

Writes into the dropbox:
  - sample_charter.pdf        (2 pages, pseudo-16th-century Portuguese charter)
  - sample_charter.prompt.md  (custom per-file extraction prompt -> JSON)
  - sample_charter_image.png  (page 1 as an image, extracted with the default prompt)
"""

from pathlib import Path

import pymupdf as fitz  # pymupdf (fitz API)

ROOT = Path(__file__).resolve().parent.parent
DROP = ROOT / "dropbox"

PAGE1 = [
    "Traslado da carta de doação que el-rei D. Manuel fez ao mosteiro de São Bento de Lisboa, em o ano de mil quatrocentos e noventa e oito.",
    "Dom Manuel, pela graça de Deus rei de Portugal e dos Algarves, d'aquem e d'alem mar em Africa, senhor de Guiné, faço saber a quantos esta carta virem que, considerando os muitos e bons serviços que o abade Frei João de São Bento, e seus religiosos, nos tem feitos, lhes dou e outorgo a herdade de Alfange, termo de Évora, com seus pomares, vinhas e moinhos, para sempre.",
    "E mando que o dito mosteiro haja e possua a dita herdade, livre e desembargada, sem embargo de quaisquer direitos reais que nela possamos haver. E por firmeza de todo mandei passar esta carta, assinada por mim e selada com o meu selo pendente.",
    "Dada em Lisboa, aos vinte e sete dias do mês de Novembro, o dito ano de mil quatrocentos e noventa e oito. — El-rei o mandou pelos desembargadores do paço.",
]

PAGE2 = [
    "E notifiquei aos oficiais da câmara de Évora que dessem posse ao dito mosteiro da dita herdade, o que foi feito em presença de testemunhas: Pero Vaz Caminha, escrivão, e João Álvares, morador em Évora.",
    "E o escrivão da câmara fez o auto de posse, que ficou registado no livro dos acordos, a folhas cento e doze.",
    "Marginalia: veja-se o traslado do tombo do mosteiro, fl. 45.",
    "Assinado: Frei João de São Bento, abade. — Selo pendente de cera vermelha, com as armas reais.",
]

PROMPT = """Extract the information from this archival document and return it as JSON with these exact keys:
- document_type: what kind of document it is (e.g. charter, letter, inventory)
- language: language(s) used
- date: explicit date(s) found, in the original form
- parties: people/institutions involved (list)
- places: places mentioned (list)
- summary: 3-5 sentence summary in English
- transcription: faithful transcription of the text, keeping original spelling, marking illegible parts with [illegible]
- archival_marks: shelfmarks, page/folio numbers, stamps, seal descriptions

If a piece of information is not present, use null. Output ONLY the JSON object."""


def main() -> None:
    DROP.mkdir(exist_ok=True)
    pdf = DROP / "sample_charter.pdf"
    doc = fitz.open()
    for page_text in (PAGE1, PAGE2):
        page = doc.new_page(width=595, height=842)  # A4
        page.insert_textbox(
            fitz.Rect(72, 72, 523, 770),
            "\n".join(page_text),
            fontname="times-roman",
            fontsize=13,
            lineheight=1.5,
        )
        page.insert_text(
            (72, 810),
            "— 1 —" if len(doc) == 1 else "— 2 —",
            fontname="times-roman",
            fontsize=11,
        )
    doc.save(str(pdf))
    doc.close()
    (DROP / "sample_charter.prompt.md").write_text(PROMPT)
    with fitz.open(str(pdf)) as d:
        d[0].get_pixmap(dpi=150).save(str(DROP / "sample_charter_image.png"))
    print("wrote:")
    for p in sorted(DROP.iterdir()):
        print(f"  {p.name}  ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
