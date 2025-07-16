import streamlit as st
import pandas as pd
import openai
import json
import os
import requests
import base64

st.set_page_config(layout="wide")

# 🔳 Styl a pozadí
def set_background(image_path, opacity=0.2):
    with open(image_path, "rb") as image_file:
        encoded = base64.b64encode(image_file.read()).decode()
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-image: url("data:image/png;base64,{encoded}");
            background-size: cover;
            background-repeat: no-repeat;
            background-attachment: fixed;
            position: relative;
        }}
        .stApp::before {{
            content: "";
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(255,255,255,{1 - opacity});
            z-index: -1;
        }}
        .main > div > div > div > div {{
            background-color: rgba(240,240,240,0.9);
            padding: 10px;
            border-radius: 10px;
        }}
        table {{
            background-color: #f5f5f5 !important;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )

set_background("grafika/pozadi_hlavni.PNG", opacity=0.2)

# 🧠 Inicializace session
if 'vysledky' not in st.session_state:
    st.session_state.vysledky = []
if 'debug_history' not in st.session_state:
    st.session_state.debug_history = ""

st.title("Asistent cenových nabídek od Davida")

# 📍 Google Maps API – výpočet vzdálenosti
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
        return data['rows'][0]['elements'][0]['distance']['value'] / 1000
    except Exception as e:
        st.error(f"❌ Chyba při získávání vzdálenosti: {e}")
        return None

# 📂 Načti všechny CSV z adresáře 'ceniky'
ceniky_dir = "ceniky"
ceniky = {}
try:
    for filename in os.listdir(ceniky_dir):
        if filename.endswith(".csv"):
            produkt = os.path.splitext(filename)[0]
            path = os.path.join(ceniky_dir, filename)
            df = pd.read_csv(path, index_col=0)
            ceniky[produkt.lower()] = df
    sheet_names = list(ceniky.keys())
    st.session_state.sheet_names = sheet_names
    st.session_state.debug_history += f"\n📄 Načtené produkty z CSV: {sheet_names}\n"
except Exception as e:
    st.error(f"❌ Chyba při načítání CSV ceníků: {e}")
    st.stop()

# 📋 Formulář
with st.form(key="vstupni_formular"):
    user_input = st.text_area(
        "Zadejte popis produktů, rozměry a místo dodání:",
        height=100,
        placeholder="Např. ALUX Glass 6000x2500 Brno, screen 3500x2500..."
    )
    submit_button = st.form_submit_button(label="📤 ODESLAT")

# 🔍 Zpracování vstupu
if submit_button and user_input:
    debug_text = f"\n---\n📥 Uživatelský vstup:\n{user_input}\n"

    with st.spinner("Analyzuji vstup pomocí GPT..."):
        try:
            client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
            gpt_prompt = (
                f"Tvůj úkol: z následujícího textu vytáhni VŠECHNY produkty, každý se svým názvem, šířkou (v mm), hloubkou nebo výškou (v mm) a místem dodání. "
                f"Název produktu vybírej co nejpřesněji z následujícího seznamu produktů: {', '.join(sheet_names)}. "
                f"POZOR: Pokud uživatel napíše 'screen', 'screenová roleta', 'boční screen' — vždy to přiřaď k produktu 'screen'. "
                f"Rozměry ve vzorcích (např. 3590-240) vždy spočítej. "
                f"Vrať POUZE validní JSON. Např. [{{\"produkt\": \"...\", \"šířka\": ..., \"hloubka_výška\": ..., \"misto\": \"...\"}}] nebo [{{\"nenalezeno\": true, \"zprava\": \"...\"}}]."
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
            debug_text += f"\n📬 GPT odpověď (RAW):\n{gpt_output_raw}\n"

            start_idx = gpt_output_raw.find('[')
            end_idx = gpt_output_raw.rfind(']') + 1
            gpt_output_clean = gpt_output_raw[start_idx:end_idx]
            debug_text += f"\n📦 GPT JSON blok:\n{gpt_output_clean}\n"

            products = json.loads(gpt_output_clean)
            debug_text += f"\n📤 GPT parsed výstup:\n{json.dumps(products, indent=2)}\n"

            all_rows = []

            if products and 'nenalezeno' in products[0]:
                zprava = products[0].get('zprava', 'Produkt nenalezen.')
                st.warning(f"❗ {zprava}")
                debug_text += f"\n⚠ {zprava}\n"
            else:
                produkt_map = {
                    "screen": "screen", "alux screen": "screen",
                    "screenová roleta": "screen", "boční screen": "screen"
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

                    df = ceniky.get(produkt_lookup)
                    if df is None:
                        st.error(f"❌ Nenalezen ceník: {produkt_lookup}")
                        debug_text += f"\n❌ Nenalezen ceník '{produkt_lookup}'\n"
                        continue

                    sloupce = sorted([int(c) for c in df.columns])
                    radky = sorted([int(r) for r in df.index])

                    debug_text += f"\n📊 Matice – šířky: {sloupce}, výšky: {radky}\n"

                    sirka_real = next((s for s in sloupce if s >= sirka), sloupce[-1])
                    vyska_real = next((v for v in radky if v >= vyska_hloubka), radky[-1])
                    debug_text += f"\n📐 Vybraná velikost: {sirka_real}×{vyska_real}\n"

                    try:
                        cena = df.loc[vyska_real, str(sirka_real)]
                        debug_text += f"\n💰 Cena nalezena: {cena} Kč\n"
                    except Exception as e:
                        st.error(f"❌ Cena nenalezena: {e}")
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
                            debug_text += f"\n🛠️ Montáž {perc}% = {cena_montaz} Kč\n"

                    if misto and misto.lower() not in ["neuvedeno", "nedodáno"]:
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
            debug_text += f"\n📦 Výsledná tabulka:\n{pd.DataFrame(all_rows).to_string(index=False)}\n"
            st.session_state.debug_history += debug_text

        except json.JSONDecodeError as e:
            st.error("❌ Chyba při zpracování JSON.")
            st.session_state.debug_history += f"\n⛔ JSONDecodeError: {e}\n"
        except Exception as e:
            st.error(f"❌ Výjimka: {e}")
            st.session_state.debug_history += f"\n⛔ Výjimka: {e}\n"

# 📋 Výpis výsledků
for idx, vysledek in enumerate(st.session_state.vysledky):
    st.write(f"### Výsledek {len(st.session_state.vysledky) - idx}")
    st.table(vysledek)

# 🐞 Debug panel (20 % výšky)
st.markdown(
    f"<div style='position: fixed; bottom: 0; left: 0; right: 0; height: 20%; overflow-y: scroll; "
    f"background-color: #f0f0f0; font-size: 10px; padding: 10px;'>"
    f"<pre>{st.session_state.debug_history}</pre></div>",
    unsafe_allow_html=True
)
