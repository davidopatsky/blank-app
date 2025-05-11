import streamlit as st
import pandas as pd
import openai
import json
import requests

# Nastavení stránky
st.set_page_config(layout="wide")

# CSS úprava: větší nadpis a šířka aplikace
st.markdown(
    """
    <style>
    .main {
        max-width: 80%;
        margin: auto;
    }
    h1 {
        font-size: 45px !important;
        margin-top: 0 !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# Inicializace session
if 'vysledky' not in st.session_state:
    st.session_state.vysledky = []
if 'debug_history' not in st.session_state:
    st.session_state.debug_history = ""

st.title("Asistent cenových nabídek od Davida")

# Funkce na výpočet vzdálenosti
def get_distance_km(origin, destination, api_key):
    url = "https://maps.googleapis.com/maps/api/distancematrix/json"
    params = {
        'origins': origin,
        'destinations': destination,
        'key': api_key,
        'units': 'metric'
    }
    response = requests.get(url, params=params)
    st.session_state.debug_history += f"\n📡 Google API Request: {response.url}\n"
    data = response.json()
    st.session_state.debug_history += f"\n📬 Google API Response:\n{json.dumps(data, indent=2)}\n"
    try:
        distance_meters = data['rows'][0]['elements'][0]['distance']['value']
        return distance_meters / 1000
    except Exception as e:
        st.error(f"❌ Chyba při získávání vzdálenosti: {e}")
        return None

# Načtení Excelu
cenik_path = "./data/ALUX_pricelist_CZK_2025 simplified chatgpt v7.xlsx"
try:
    excel_file = pd.ExcelFile(cenik_path)
    sheet_names = excel_file.sheet_names
    st.session_state.sheet_names = sheet_names
    st.session_state.debug_history += f"\n📄 Načtené záložky: {sheet_names}\n"
except Exception as e:
    st.error(f"❌ Nepodařilo se načíst Excel: {e}")
    st.stop()

# Uživatelský vstup
user_input = st.text_input("Zadejte popis produktů, rozměry a místo dodání (potvrďte Enter):")

if user_input:
    debug_text = f"\n---\n📥 Uživatelský vstup:\n{user_input}\n"

    with st.spinner("Analyzuji vstup pomocí GPT..."):
        try:
            client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
            gpt_prompt = (
                f"Tvůj úkol: z následujícího textu vytáhni VŠECHNY produkty, každý se svým názvem, šířkou (v mm), hloubkou nebo výškou (v mm) a místem dodání. "
                f"Název produktu vybírej co nejpřesněji z následujícího seznamu produktů: {', '.join(sheet_names)}. "
                f"POZOR: Pokud uživatel napíše jakoukoli z těchto frází: 'screen', 'screenová roleta', 'boční screen', 'boční screenová roleta' — VŽDY to přiřaď k produktu 'screen'. "
                f"Rozměry ve vzorcích (např. 3590-240) vždy dopočítej. "
                f"Vrať POUZE validní JSON seznam položek. Např. [{{\"produkt\": \"...\", \"šířka\": ..., \"hloubka_výška\": ..., \"misto\": \"...\"}}] nebo [{{\"nenalezeno\": true, \"zprava\": \"...\"}}]. Nepiš nic mimo JSON."
            )
            debug_text += f"\n📨 GPT prompt:\n{gpt_prompt}\n"

            response = client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[
                    {"role": "system", "content": gpt_prompt},
                    {"role": "user", "content": user_input}
                ],
                max_tokens=1000
            )

            gpt_output_raw = response.choices[0].message.content.strip()
            if not gpt_output_raw:
                raise ValueError("GPT odpověď je prázdná.")

            debug_text += f"\n📬 GPT odpověď (RAW):\n{gpt_output_raw}\n"

            start_idx = gpt_output_raw.find('[')
            end_idx = gpt_output_raw.rfind(']') + 1
            if start_idx == -1 or end_idx <= start_idx:
                raise ValueError("GPT odpověď neobsahuje validní JSON seznam.")

            gpt_output_clean = gpt_output_raw[start_idx:end_idx]
            debug_text += f"\n📦 GPT JSON blok:\n{gpt_output_clean}\n"

            products = json.loads(gpt_output_clean)
            all_rows = []

            if products and 'nenalezeno' in products[0]:
                zprava = products[0].get('zprava', 'Produkt nenalezen.')
                st.warning(f"❗ {zprava}")
                debug_text += f"\n⚠ {zprava}\n"
            else:
                produkt_map = {
                    "screen": "screen", "alux screen": "screen",
                    "screenová roleta": "screen", "screenova roleta": "screen",
                    "boční screen": "screen", "boční screenová roleta": "screen"
                }

                for params in products:
                    produkt = params['produkt'].strip().lower()
                    produkt_lookup = produkt_map.get(produkt, produkt)
                    misto = params.get("misto", "")

                    try:
                        sirka = int(float(params['šířka']))
                        vyska_hloubka = (
                            2500 if params['hloubka_výška'] is None and "screen" in produkt_lookup
                            else int(float(params['hloubka_výška']))
                        )
                    except Exception as e:
                        st.error(f"❌ Chybný rozměr: {e}")
                        debug_text += f"\n❌ Chybný rozměr: {e}\n"
                        continue

                    debug_text += f"\n🔍 Produkt: {produkt_lookup}, rozměr: {sirka}×{vyska_hloubka}, místo: {misto}\n"

                    sheet_match = next((s for s in sheet_names if s.lower() == produkt_lookup), None)
                    if not sheet_match:
                        sheet_match = next((s for s in sheet_names if produkt_lookup in s.lower()), None)

                    if not sheet_match:
                        st.error(f"❌ Nenalezena záložka: {produkt_lookup}")
                        debug_text += f"\n❌ Nenalezena záložka '{produkt_lookup}'\n"
                        continue

                    df = pd.read_excel(cenik_path, sheet_name=sheet_match, index_col=0)

                    # OPRAVA: načti sloupce a řádky bez mezer a s čárkou jako desetinnou tečkou
                    sloupce = sorted([
                        int(float(str(c).replace(" ", "").replace(",", ".")))
                        for c in df.columns
                        if isinstance(c, str) or isinstance(c, (int, float))
                    ])
                    radky = sorted([
                        int(float(str(r).replace(" ", "").replace(",", ".")))
                        for r in df.index
                        if isinstance(r, str) or isinstance(r, (int, float))
                    ])

                    if not sloupce or not radky:
                        st.error(f"❌ Záložka '{sheet_match}' neobsahuje platné číselné rozměry.")
                        debug_text += f"\n❌ Chybí číselné řádky nebo sloupce v záložce '{sheet_match}'\n"
                        continue

                    debug_text += f"\n📊 Matice – šířky: {sloupce}, výšky: {radky}\n"

                    sirka_real = next((s for s in sloupce if s >= sirka), sloupce[-1])
                    vyska_real = next((v for v in radky if v >= vyska_hloubka), radky[-1])
                    debug_text += f"\n📐 Použité rozměry v ceníku: {sirka_real}×{vyska_real}\n"

                    try:
                        cena = df.loc[str(vyska_real), str(sirka_real)]
                        debug_text += f"\n💰 Nalezená cena: {cena} Kč\n"
                    except Exception as e:
                        st.error(f"❌ Nenalezena cena pro {sirka_real}×{vyska_real}")
                        debug_text += f"\n❌ Chyba při čtení ceny: {e}\n"
                        continue

                    all_rows.append({
                        "POLOŽKA": produkt_lookup,
                        "ROZMĚR": f"{sirka} × {vyska_hloubka} mm",
                        "CENA bez DPH": round(float(cena))
                    })

                    if "screen" not in produkt_lookup:
                        for perc in [12, 13, 14, 15]:
                            cena_montaz = round(float(cena) * perc / 100)
                            all_rows.append({
                                "POLOŽKA": f"Montáž {perc}%",
                                "ROZMĚR": "",
                                "CENA bez DPH": cena_montaz
                            })
                            debug_text += f"\n🛠️ Montáž {perc}%: {cena_montaz} Kč\n"

                    if misto and misto.lower() != "nedodáno":
                        api_key = st.secrets["GOOGLE_API_KEY"]
                        distance_km = get_distance_km("Blučina, Czechia", misto, api_key)
                        if distance_km:
                            cena_doprava = round(distance_km * 2 * 15)
                            all_rows.append({
                                "POLOŽKA": "Doprava",
                                "ROZMĚR": f"{distance_km:.1f} km",
                                "CENA bez DPH": cena_doprava
                            })
                            debug_text += f"\n🚚 Doprava {distance_km:.1f} km = {cena_doprava} Kč\n"

            st.session_state.vysledky.insert(0, all_rows)
            st.session_state.debug_history += debug_text

        except json.JSONDecodeError as e:
            st.error("❌ Chyba při zpracování JSON.")
            debug_text += f"\n⛔ JSONDecodeError: {e}\n"
            st.session_state.debug_history += debug_text
        except Exception as e:
            st.error(f"❌ Výjimka: {e}")
            debug_text += f"\n⛔ Výjimka: {e}\n"
            st.session_state.debug_history += debug_text

# Výpis výsledků
for idx, vysledek in enumerate(st.session_state.vysledky):
    st.write(f"### Výsledek {len(st.session_state.vysledky) - idx}")
    st.table(vysledek)

# Debug panel
st.markdown(
    f"<div style='position: fixed; bottom: 0; left: 0; right: 0; height: 40%; overflow-y: scroll; "
    f"background-color: #f0f0f0; font-size: 10px; padding: 10px;'>"
    f"<pre>{st.session_state.debug_history}</pre></div>",
    unsafe_allow_html=True
)
