#!/usr/bin/env python3
"""Morning Brief v4"""
import calendar as _cal
import feedparser
import html as html_lib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
try:
    from zoneinfo import ZoneInfo
    _PARIS = ZoneInfo("Europe/Paris")
    # Validate it actually applies DST correctly
    _test = datetime(2026, 5, 1, tzinfo=_PARIS).utcoffset().seconds // 3600
    if _test not in (1, 2):
        raise ValueError("bad offset")
except Exception:
    # Fallback: compute Paris offset from UTC (CET=+1 Oct-Mar, CEST=+2 Apr-Sep)
    from datetime import timedelta as _td
    class _ParisTZ(timezone):
        def utcoffset(self, dt):
            m = dt.month if dt else 5
            return _td(hours=2 if 3 < m <= 10 else 1)
        def tzname(self, dt): return "CEST" if (dt and 3 < dt.month <= 10) else "CET"
        def dst(self, dt): return _td(hours=1) if (dt and 3 < dt.month <= 10) else _td(0)
    _PARIS = _ParisTZ()
OUTPUT_DIR          = Path(__file__).parent
OUTPUT_FILE         = OUTPUT_DIR / "index.html"
CONFIG_FILE         = OUTPUT_DIR / "telegram_config.json"
SESSION_FILE        = OUTPUT_DIR / "telegram_session"
OPENAI_CONFIG_FILE  = OUTPUT_DIR / "openai_config.json"
HEADLINE_CACHE_FILE = OUTPUT_DIR / "headline_cache.json"
MAX_PER_SOURCE = 100   # effectively uncapped — 24h filter does the work
# Sources that publish weekly or less — get a 7-day window instead of 24h
WEEKLY_SOURCES = frozenset([
    "Not Boring", "Silicon Carne", "TBPN", "SiliconMania",
    "Le Monde Marseille", "Marsactu", "Le Monde Paris",
])
# Map sub-feeds to their canonical publication name for exact-dupe collapsing
SOURCE_CANONICAL = {
    "FT Tech":           "FT",
    "FT Companies Tech": "FT",
    "Les Echos tech":    "Les Echos",
    "Les Echos macro":   "Les Echos",
    "Les Echos PACA":    "Les Echos",
    "BBC Sport":         "BBC",
    "BBC World":         "BBC",
    "Reuters World":     "Reuters",
    "Reuters Top":       "Reuters",
    "Le Monde Int":      "Le Monde",
    "Le Monde Marseille":"Le Monde",
    "Le Monde Paris":    "Le Monde",
}
# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_CHANNELS = [
    ("AFP", "https://t.me/+5VtjHHeuarNjYTBk"),
]
TELEGRAM_LIMIT = 30
# ── AFP routing keywords ──────────────────────────────────────────────────────
TECH_KEYWORDS = [
    "ai ", "startup", "ipo", "fundrais", "venture capital", "silicon", "tech",
    "software", "openai", "anthropic", "apple", "google", "microsoft", "amazon",
    "meta ", "nvidia", "semiconductor", "chip", "robot", "crypto", "bitcoin",
    "intelligence artificielle", "numérique", "levée de fonds",
]
MACRO_KEYWORDS = [
    "economy", "inflation", "fed ", "ecb ", "interest rate", "gdp", "recession",
    "market", "stock", "bond ", "currency", "euro ", "dollar", "oil ", "commodities",
    "bce ", "taux ", "économie", "bourse", "croissance", "banque centrale",
    "trade", "tariff", "budget", "fiscal", "deficit", "debt ",
    # crypto
    "bitcoin", "ethereum", "crypto", "blockchain", "defi", "stablecoin",
    "coindesk", "the block", "web3", "token ", "altcoin", "btc ", "eth ",
]
# ══════════════════════════════════════════════════════════════════════════════
#  CONFLICTS
# ══════════════════════════════════════════════════════════════════════════════
CONFLICTS = [
    {"id":"ukraine",  "name":"Ukraine — Russia",          "type":"conflict","lat":49.0, "lon":31.5,
     "started":"February 2022",
     "summary":"Russia launched a full-scale invasion of Ukraine in February 2022, triggering the largest armed conflict in Europe since World War II. Fighting continues along an approximately 1,000 km eastern front across Donbas, Zaporizhzhia, and Kharkiv oblasts, with Russian forces making slow incremental gains. Ukraine has responded with long-range drone strikes deep into Russian territory, hitting oil refineries, airfields, and Moscow itself. NATO allies have committed over $100B in military and financial aid, including F-16s, ATACMS, and Patriot systems. Casualty figures on both sides are estimated in the hundreds of thousands. Diplomatic efforts remain deadlocked; no credible peace framework has emerged and the war shows no sign of ending.",
     "keywords":["ukraine","russia","kyiv","zelenskyy","zelensky","donbas","kharkiv","kremlin","zaporizhzhia","donetsk","luhansk"]},
    {"id":"gaza",     "name":"Gaza — Israel",             "type":"conflict","lat":31.5, "lon":34.5,
     "started":"October 2023",
     "summary":"Hamas's mass attack on Israel on 7 October 2023 killed approximately 1,200 Israelis and took 250 hostages, triggering Israel's most intense military campaign in decades. The Gaza Strip has been subjected to sustained bombardment and ground operations, with Palestinian death tolls exceeding 45,000 according to Gaza health authorities, the majority civilians. A brief ceasefire in late 2023 allowed some hostage releases but fighting quickly resumed. The UN has declared famine conditions in northern Gaza; virtually all civilian infrastructure has been destroyed. International pressure for a permanent ceasefire has intensified but negotiations between Hamas and Israel, mediated by Qatar and Egypt, have repeatedly collapsed. The West Bank has simultaneously seen rising settler violence and military raids.",
     "keywords":["gaza","israel","hamas","netanyahu","rafah","west bank","idf","ceasefire","hostage","palestin"]},
    {"id":"sudan",    "name":"Sudan Civil War",            "type":"conflict","lat":15.5, "lon":32.5,
     "started":"April 2023",
     "summary":"War between the Sudanese Armed Forces (SAF) and the paramilitary Rapid Support Forces (RSF) erupted in April 2023, rapidly engulfing Khartoum and spreading to Darfur and Kordofan. Over 12 million people have been displaced — one of the world's worst humanitarian crises — with famine declared in several regions. RSF forces have committed widespread atrocities in Darfur that international investigators have characterised as genocide, echoing the 2003 massacres. Khartoum has been devastated by sustained urban combat, its infrastructure reduced to rubble. International attention and humanitarian access remain grossly insufficient; no meaningful peace process is underway.",
     "keywords":["sudan","rsf","darfur","khartoum","sudanese armed"]},
    {"id":"yemen",    "name":"Yemen",                     "type":"conflict","lat":15.5, "lon":47.5,
     "started":"2014",
     "summary":"The Houthi movement (Ansar Allah), backed by Iran, controls northern Yemen including Sanaa. Since October 2023, Houthis have launched hundreds of drone and missile strikes on commercial and military shipping in the Red Sea and Gulf of Aden, forcing major container lines to reroute around Africa and driving up global freight costs. The US, UK, and allied forces have conducted extensive retaliatory strikes on Houthi weapons infrastructure, with limited lasting effect. Separately, a fragile UN-brokered truce between Houthis and the Saudi-backed government has reduced large-scale ground combat in much of the country, though no political settlement is in sight and Yemen remains one of the world's worst humanitarian disasters.",
     "keywords":["yemen","houthi","houthis","red sea attack","aden","sanaa"]},
    {"id":"myanmar",  "name":"Myanmar",                   "type":"conflict","lat":19.0, "lon":96.5,
     "started":"February 2021",
     "summary":"A broad resistance coalition — including the People's Defence Force (PDF) and ethnic armed organisations (EAOs) such as the Arakan Army, TNLA, and MNDAA — has made dramatic territorial gains against the military junta since a coordinated offensive launched in late 2023. Key border towns including Laukkai, Myawaddy, and significant stretches of Shan State have fallen to resistance forces, severing junta control of major trade routes to China and Thailand. The military has lost over a third of its territory and introduced conscription as manpower dwindles. China has mediated fragile ceasefires in border areas to protect its Belt and Road investments, while the US and EU have tightened sanctions on the regime.",
     "keywords":["myanmar","burma","junta","arakan army","tatmadaw","shan state","mandalay"]},
    {"id":"sahel",    "name":"Sahel — Mali / Burkina / Niger","type":"conflict","lat":14.0,"lon":-2.0,
     "started":"2012",
     "summary":"A belt of jihadist insurgency linked to Islamic State Sahel Province (ISSP) and JNIM (al-Qaeda affiliate) continues to expand across Mali, Burkina Faso, and Niger, displacing millions and collapsing state authority across vast rural areas. Military juntas ruling all three countries have expelled French and other Western forces and invited Russia's Africa Corps (formerly Wagner Group) as security partners — a shift that has not improved security outcomes. Burkina Faso's junta has lost control of an estimated 40–60% of national territory. ECOWAS sanctions have proved ineffective. The region faces acute food insecurity and has become a launchpad for attacks reaching coastal West African states including Benin, Togo, and Ghana.",
     "keywords":["sahel","mali","burkina faso","niger junta","aqim","jnim","africa corps","bamako","ouagadougou"]},
    {"id":"drc",      "name":"DR Congo",                  "type":"conflict","lat":-2.5, "lon":28.5,
     "started":"1990s",
     "summary":"M23 rebels, armed and directed by Rwanda according to UN experts, have seized substantial territory in eastern DRC including key parts of Goma, the commercial capital of North Kivu with a population of over one million. The offensive is the most significant territorial shift in the Congo's decades-long conflict and has displaced over six million people in the east alone. Direct DRC-Rwanda confrontation risk is high, with both governments exchanging artillery fire across the border. MONUSCO, the UN peacekeeping mission, is drawing down amid widespread hostility. The Nairobi and Luanda peace processes have produced repeated ceasefires that collapse within days. Coltan and gold mines remain central to the conflict economy.",
     "keywords":["drc","congo","m23","goma","kivu","rwanda drc","kinshasa","afd congo"]},
    {"id":"somalia",  "name":"Somalia",                   "type":"conflict","lat":5.0,  "lon":46.0,
     "started":"2006",
     "summary":"Al-Shabaab controls large swathes of south-central Somalia and continues to mount sophisticated attacks in Mogadishu and increasingly in Kenya and Ethiopia. Despite losing some territory to Somali National Army offensives in 2022–23, the group has rebounded and continues to raise funds through taxation and extortion across its territory. The AU Transition Mission (ATMIS) is drawing down on schedule, creating security vacuums that al-Shabaab is actively exploiting. The federal government in Mogadishu is beset by clan disputes and fiscal crisis. Drought and recurrent flooding have driven acute food insecurity, making Somalia one of the world's most complex humanitarian emergencies.",
     "keywords":["somalia","al-shabaab","mogadishu","atmis","al shabaab"]},
    {"id":"haiti",    "name":"Haiti",                     "type":"conflict","lat":18.9, "lon":-72.3,
     "started":"2021",
     "summary":"Armed gang coalitions led by the G9 federation and the Viv Ansanm alliance have seized control of much of Port-au-Prince and key provincial towns, triggering a full humanitarian and governance collapse. Hospitals, schools, and government offices have been overwhelmed or shut entirely. A Kenyan-led Multinational Security Support Mission (MSS) deployed in mid-2024 but is severely under-resourced and has struggled to make security gains. Over 700,000 people are internally displaced; famine conditions have been declared. Kidnapping for ransom is endemic across the country. A transitional presidential council is attempting to stabilise governance ahead of planned elections, but the political roadmap remains contested.",
     "keywords":["haiti","gang haiti","port-au-prince","kenyan mission"]},
    {"id":"colombia", "name":"Colombia",                  "type":"conflict","lat":4.0,  "lon":-72.0,
     "started":"ongoing",
     "summary":"Dissident FARC factions (Estado Mayor Central and Segunda Marquetalia) and the ELN guerrillas continue active operations across border regions with Venezuela and Ecuador, the Pacific coast, and the Catatumbo region. President Petro's flagship 'total peace' policy led to multiple negotiated ceasefires, virtually all of which have collapsed. The Catatumbo conflict in early 2024 saw intense fighting between the ELN and FARC dissidents, displacing over 30,000 civilians. Coca production and cocaine trafficking continue at record levels, funding all armed groups. Violence against social leaders, indigenous communities, and former FARC combatants who demobilised under the 2016 peace deal remains a persistent and largely unpunished problem.",
     "keywords":["colombia","eln","farc","catatumbo","petro colombia"]},
    {"id":"mozambique","name":"Mozambique",               "type":"conflict","lat":-13.0,"lon":39.5,
     "started":"2017",
     "summary":"An Islamist insurgency (Ansar al-Sunna Muhammadiyah, known locally as Al-Shabaab) has persisted in Cabo Delgado province despite Rwandan and SADC military deployments since 2021, displacing nearly a million people and keeping major LNG projects including TotalEnergies' $20B Afungi facility on indefinite suspension. Post-election unrest following the contested October 2024 presidential election resulted in over 300 deaths, with opposition leader Venâncio Mondlane disputing results from exile and calling for sustained protests. The combination of insurgency and political crisis has left Mozambique facing serious instability on multiple fronts simultaneously.",
     "keywords":["mozambique","cabo delgado","ansar al-sunna"]},
    {"id":"taiwan",   "name":"Taiwan Strait",             "type":"tension", "lat":23.5, "lon":120.5,
     "started":"ongoing",
     "summary":"The PLA conducts increasingly frequent and sophisticated military exercises around Taiwan, including multi-day air and sea encirclement drills that simulate a blockade. China's grey-zone operations — using coast guard vessels, maritime militia, and daily incursions into Taiwan's air defence identification zone — have intensified under Xi Jinping. US arms sales, Congressional visits, and Taiwan's own defence budget increases have further heightened cross-strait tensions. Taiwan's president Lai Ching-te, elected in January 2024, maintains a firm stance while avoiding direct provocation. TSMC's dominance of advanced semiconductor manufacturing gives Taiwan extraordinary global strategic weight, making a Chinese move against the island a direct threat to global supply chains.",
     "keywords":["taiwan","pla taiwan","beijing taiwan","taiwan strait","tsmc"]},
    {"id":"southchinasea","name":"South China Sea",       "type":"tension", "lat":12.0, "lon":115.0,
     "started":"ongoing",
     "summary":"China and the Philippines are engaged in near-daily confrontations at Second Thomas Shoal (Ayungin Shoal), where Manila maintains a deliberate military outpost on the deliberately grounded vessel BRP Sierra Madre. Chinese coast guard vessels have used water cannons, military-grade lasers, and physical obstruction against Philippine resupply missions, injuring Filipino sailors and seizing supplies. The Philippines has invoked its mutual defence treaty with the US, which has reinforced its alliance posture and stationed additional forces at new Philippine bases. ASEAN negotiations for a Code of Conduct in the South China Sea have stalled. Vietnam, Malaysia, and Brunei also have contested overlapping claims.",
     "keywords":["south china sea","spratlys","second thomas shoal","philippines china sea","paracel"]},
    {"id":"northkorea","name":"North Korea",              "type":"tension", "lat":40.0, "lon":127.0,
     "started":"ongoing",
     "summary":"North Korea has deployed an estimated 10,000–12,000 troops to Russia to support operations in Ukraine, representing a significant and unprecedented internationalisation of Pyongyang's military posture. In exchange, North Korea is believed to be receiving advanced missile and satellite technology, conventional weapons, and economic relief. Kim Jong-un has conducted multiple ICBM tests demonstrating the capability to strike the US mainland. Pyongyang has formally declared South Korea a hostile foreign state, dismantled inter-Korean liaison infrastructure, and launched balloons carrying trash and propaganda into the South. North-South relations are at their lowest point in three decades.",
     "keywords":["north korea","pyongyang","kim jong","dprk","north korean troops"]},
    {"id":"iran",     "name":"Iran",                      "type":"tension", "lat":32.0, "lon":53.0,
     "started":"ongoing",
     "summary":"Iran's uranium enrichment has reached 60% purity — close to the 90% weapons-grade threshold — at facilities where IAEA inspectors have significantly reduced access. Tehran's 'Axis of Resistance' proxy network — Hezbollah, Hamas, Houthis, and various Iraqi Shia militias — has been severely weakened but not dismantled following Israel's 2024 campaign. A direct Iran-Israel exchange of fire in April 2024 (300+ Iranian drones and missiles followed by an Israeli retaliatory strike) marked a historic first direct confrontation between the two countries. Intermittent US-Iran nuclear negotiations continue but have not produced a successor agreement to the 2015 JCPOA. Iran's path to nuclear weapons capability is the central strategic concern animating Middle Eastern security.",
     "keywords":["iran","tehran","iranian","irgc","nuclear iran","khamenei"]},
    {"id":"venezuela","name":"Venezuela",                 "type":"tension", "lat":8.0,  "lon":-66.0,
     "started":"2019",
     "summary":"Nicolás Maduro claimed victory in the July 2024 presidential election despite opposition candidate Edmundo González demonstrably winning according to independently verified voting tallies from over 80% of polling stations. The regime deployed massive repression against post-election protests, with over 2,400 arrested and more than two dozen killed. González went into exile while opposition leader María Corina Machado remains in Venezuela under constant threat of arrest, continuing to organise from hiding. The US, EU, and most Latin American democracies have refused to recognise Maduro's claimed mandate. Venezuela's migrant diaspora now exceeds 7.7 million — one of the world's largest displacement crises — driven by economic collapse and political persecution.",
     "keywords":["venezuela","maduro","caracas","edmundo gonzalez"]},
    {"id":"syria",    "name":"Syria",                     "type":"tension", "lat":35.0, "lon":38.0,
     "started":"2011",
     "summary":"HTS (Hayat Tahrir al-Sham) led a lightning offensive in late November 2024 that toppled Bashar al-Assad's government in just eleven days, ending 54 years of Assad family rule. A transitional government led by Ahmed al-Sharaa (formerly Abu Mohammad al-Jolani) is consolidating control in major cities while navigating competing factions, IS sleeper cell attacks, and a contested periphery. Turkish forces and Turkey-backed groups hold the north, US-backed Kurdish SDF forces control the northeast, and Israel has conducted hundreds of airstrikes destroying Syrian military infrastructure. International sanctions relief is conditional on transition governance credibility. Syria faces one of the world's most acute reconstruction challenges with an estimated $400B in war damage.",
     "keywords":["syria","damascus","hts","hayat tahrir","post-assad","syrian"]},
    {"id":"kosovo",   "name":"Kosovo / Balkans",          "type":"tension", "lat":42.5, "lon":21.0,
     "started":"ongoing",
     "summary":"Serbia refuses to recognise Kosovo's 2008 declaration of independence, backed internationally by Russia and China. The ethnic Serb-majority north of Kosovo operates parallel institutions funded by Belgrade, creating persistent governance friction. Periodic flashpoints — including armed incidents at the Jarinje border crossing and clashes over Kosovo's attempts to assert authority in the north — have kept NATO's KFOR peacekeeping force on heightened alert. The EU-brokered Brussels Agreement to normalise relations has not been implemented by either side. Serbia's President Vučić navigates between EU accession aspirations and traditional Moscow alignment, complicating Western leverage.",
     "keywords":["kosovo","serbia kosovo","pristina","vucic"]},
    {"id":"ethiopia", "name":"Ethiopia",                  "type":"tension", "lat":13.0, "lon":39.0,
     "started":"ongoing",
     "summary":"The November 2022 Tigray ceasefire has held but its implementation — including Tigray People's Liberation Front disarmament, accountability for atrocities, and Eritrean troop withdrawal — is severely incomplete. A simultaneous Amhara insurgency (the Fano militia) has opened a second front, with fighting in and around Gondar, Bahir Dar, and the Amhara highlands. The Oromo Liberation Army continues operations in Oromia. Prime Minister Abiy Ahmed's government faces simultaneous insurgencies it cannot fully suppress while also pursuing an assertive foreign policy, including a push for Red Sea access that is straining relations with Somalia, Eritrea, and Djibouti. Ethiopia hosts Africa's largest refugee population while generating new displacement internally.",
     "keywords":["ethiopia","tigray","amhara","oromia","addis ababa conflict"]},
    {"id":"lebanon",  "name":"Lebanon",                   "type":"tension", "lat":33.9, "lon":35.5,
     "started":"ongoing",
     "summary":"A ceasefire between Israel and Hezbollah went into effect in November 2024 following Hezbollah's most severe setbacks in decades — including the assassination of Secretary-General Hassan Nasrallah, the killing of most of its senior military command, and the destruction of its missile arsenal. Lebanon elected a new president and formed a functioning government for the first time in years, signalling a fragile political opening. However, Hezbollah remains armed, politically entrenched, and backed by Iran. Reconstruction of southern Lebanon will cost an estimated $10B. The Lebanese economy — already in freefall since the 2019 financial collapse — is struggling to attract the international support needed for stabilisation and recovery.",
     "keywords":["lebanon","hezbollah","beirut","lebanese"]},
]
# ══════════════════════════════════════════════════════════════════════════════
#  NEWS SOURCES
# ══════════════════════════════════════════════════════════════════════════════
TECH_SOURCES = [
    ("Silicon Carne",       "https://siliconcarne.substack.com/feed"),
    ("Not Boring",          "https://www.notboring.co/feed"),
    ("TBPN",                "https://tbpn.substack.com/feed"),
    # FT Tech: direct section feed works with browser-like headers (25 articles)
    ("FT Tech",             "https://www.ft.com/technology?format=rss"),
    # FT Companies/Tech: slightly different selection, good overlap coverage
    ("FT Companies Tech",   "https://www.ft.com/companies/technology?format=rss"),
    ("The NBS",             "https://news.google.com/rss/search?q=site:the-nbs.fr&hl=fr&gl=FR&ceid=FR:fr"),
    # Les Echos fetched separately via _fetch_les_echos_tech() — Python-side keyword filtering
    ("SiliconMania",        "https://news.google.com/rss/search?q=site:siliconmania.tv&hl=fr&gl=FR&ceid=FR:fr"),
    # Added
    ("MTS Newsletter",      "https://mtslive.substack.com/feed"),
    ("TechCrunch",          "https://techcrunch.com/feed/"),
    ("First Round Review",  "https://news.google.com/rss/search?q=site:review.firstround.com&hl=en&gl=US&ceid=US:en"),
    ("Lenny's Newsletter",  "https://www.lennysnewsletter.com/feed"),
    ("Pragmatic Engineer",  "https://newsletter.pragmaticengineer.com/feed"),
]
MACRO_SOURCES = [
    # GN site:ft.com returns ~100 articles vs homepage RSS's 10
    ("FT",            "https://news.google.com/rss/search?q=site:ft.com&hl=en&gl=US&ceid=US:en"),
    ("The Economist", "https://www.economist.com/the-world-this-week/rss.xml"),
    # Les Echos fetched separately via _fetch_les_echos_macro() — Python-side keyword filtering
    # Added
    ("The Street",    "https://news.google.com/rss/search?q=site:thestreet.com&hl=en&gl=US&ceid=US:en"),
    # Crypto
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("The Block",     "https://www.theblock.co/rss.xml"),
]
CULTURE_SOURCES = [
    ("NSS Magazine",      "https://news.google.com/rss/search?q=site:nssmag.com&hl=en&gl=US&ceid=US:en"),
    ("Hypebeast",         "https://hypebeast.com/feed"),
    ("Dezeen",            "https://www.dezeen.com/feed/"),
    ("W Magazine",        "https://news.google.com/rss/search?q=site:wmagazine.com&hl=en&gl=US&ceid=US:en"),
]
# NYT Arts is fetched separately and always pinned (5 latest guaranteed)
ART_NEWSPAPER_FEED = "https://rss.nytimes.com/services/xml/rss/nyt/Arts.xml"
SPORTS_SOURCES_FR = [
    ("L'Équipe", "https://news.google.com/rss/search?q=site:lequipe.fr&hl=fr&gl=FR&ceid=FR:fr"),
]
SPORTS_SOURCES_INT = [
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml"),
]
CONFLICT_NEWS_SOURCES = [
    # ── Broad wire / world feeds ──────────────────────────────────────────────
    ("Reuters World",  "https://feeds.reuters.com/reuters/worldNews"),
    ("Reuters Top",    "https://feeds.reuters.com/reuters/topNews"),
    ("BBC World",      "http://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera",     "https://www.aljazeera.com/xml/rss/all.xml"),
    # ── Quality press ─────────────────────────────────────────────────────────
    ("FT",             "https://news.google.com/rss/search?q=site:ft.com&hl=en&gl=US&ceid=US:en"),
    ("Le Monde Int",   "https://news.google.com/rss/search?q=site:lemonde.fr/international&hl=fr&gl=FR&ceid=FR:fr"),
    ("Les Echos",      "https://news.google.com/rss/search?q=site:lesechos.fr&hl=fr&gl=FR&ceid=FR:fr"),
    ("Defense News",   "https://news.google.com/rss/search?q=site:defensenews.com&hl=en&gl=US&ceid=US:en"),
    # ── Conflict-specific keyword searches (ensure coverage of niche zones) ──
    ("GN Sudan",       "https://news.google.com/rss/search?q=sudan+war+rsf+darfur&hl=en&gl=US&ceid=US:en"),
    ("GN Myanmar",     "https://news.google.com/rss/search?q=myanmar+junta+arakan+resistance&hl=en&gl=US&ceid=US:en"),
    ("GN DRC",         "https://news.google.com/rss/search?q=drc+congo+m23+goma+kivu&hl=en&gl=US&ceid=US:en"),
    ("GN Sahel",       "https://news.google.com/rss/search?q=sahel+mali+burkina+niger+jihadist&hl=en&gl=US&ceid=US:en"),
    ("GN Somalia",     "https://news.google.com/rss/search?q=somalia+al-shabaab+mogadishu&hl=en&gl=US&ceid=US:en"),
    ("GN Haiti",       "https://news.google.com/rss/search?q=haiti+gang+violence+port-au-prince&hl=en&gl=US&ceid=US:en"),
    ("GN Colombia",    "https://news.google.com/rss/search?q=colombia+eln+farc+conflict+catatumbo&hl=en&gl=US&ceid=US:en"),
    ("GN Mozambique",  "https://news.google.com/rss/search?q=mozambique+cabo+delgado+insurgency&hl=en&gl=US&ceid=US:en"),
    ("GN Ethiopia",    "https://news.google.com/rss/search?q=ethiopia+tigray+amhara+conflict&hl=en&gl=US&ceid=US:en"),
    ("GN Venezuela",   "https://news.google.com/rss/search?q=venezuela+maduro+opposition+crisis&hl=en&gl=US&ceid=US:en"),
    ("GN Kosovo",      "https://news.google.com/rss/search?q=kosovo+serbia+tension+balkans&hl=en&gl=US&ceid=US:en"),
    # ── Conflicts relying on broad feeds — dedicated fallback searches ─────
    ("GN Ukraine",     "https://news.google.com/rss/search?q=ukraine+russia+war+frontline+zelenskyy&hl=en&gl=US&ceid=US:en"),
    ("GN Gaza",        "https://news.google.com/rss/search?q=gaza+israel+hamas+ceasefire+rafah&hl=en&gl=US&ceid=US:en"),
    ("GN Yemen",       "https://news.google.com/rss/search?q=houthi+yemen+red+sea+attack+shipping&hl=en&gl=US&ceid=US:en"),
    ("GN Taiwan",      "https://news.google.com/rss/search?q=taiwan+china+pla+military+strait&hl=en&gl=US&ceid=US:en"),
    ("GN SCS",         "https://news.google.com/rss/search?q=south+china+sea+philippines+shoal+coast+guard&hl=en&gl=US&ceid=US:en"),
    ("GN NKorea",      "https://news.google.com/rss/search?q=north+korea+kim+missile+pyongyang+troops&hl=en&gl=US&ceid=US:en"),
    ("GN Iran",        "https://news.google.com/rss/search?q=iran+nuclear+iaea+sanctions+enrichment&hl=en&gl=US&ceid=US:en"),
    ("GN Syria",       "https://news.google.com/rss/search?q=syria+hts+damascus+transition+reconstruction&hl=en&gl=US&ceid=US:en"),
    ("GN Lebanon",     "https://news.google.com/rss/search?q=lebanon+hezbollah+ceasefire+reconstruction&hl=en&gl=US&ceid=US:en"),
    # Note: AFP (Telegram) is fetched separately via _fetch_telegram() and merged in main()
]
PARIS_SOURCES = [
    # Direct event guides
    ("Sortir à Paris", "https://www.sortiraparis.com/rss/"),
    ("Timeout Paris",  "https://www.timeout.com/paris/rss"),
    # Google News fallbacks — topic-specific so any source can surface
    ("Expos Paris",    "https://news.google.com/rss/search?q=exposition+paris+mus%C3%A9e+OR+galerie+OR+vernissage&hl=fr&gl=FR&ceid=FR:fr"),
    ("Sorties Paris",  "https://news.google.com/rss/search?q=agenda+paris+concert+OR+th%C3%A9%C3%A2tre+OR+spectacle+OR+danse+OR+ballet&hl=fr&gl=FR&ceid=FR:fr"),
]
CITIES_SOURCES = [
    # Marseille — curated city section + local investigative
    ("Le Monde Marseille", "https://www.lemonde.fr/marseille/rss_full.xml"),
    ("Marsactu",           "https://marsactu.fr/feed/"),
    # Paris — curated city section + regional daily
    ("Le Monde Paris",     "https://www.lemonde.fr/paris/rss_full.xml"),
    ("Le Parisien",        "https://news.google.com/rss/search?q=%22Paris%22+%22arrondissement%22+OR+%22Seine%22+OR+%22Île-de-France%22+site:leparisien.fr&hl=fr&gl=FR&ceid=FR:fr"),
]
# For build_cities: identify which sources are Marseille vs Paris
MARSEILLE_SOURCE_NAMES = {"Le Monde Marseille", "Marsactu"}
# ══════════════════════════════════════════════════════════════════════════════
#  CALENDAR
# ══════════════════════════════════════════════════════════════════════════════
CALENDAR_EVENTS = [
    # ══ 2026 ══════════════════════════════════════════════════════════════════
    # January
    {"name":"CES Las Vegas",                "start":"2026-01-06","end":"2026-01-09","cat":"tech"},
    {"name":"Davos / WEF",                  "start":"2026-01-19","end":"2026-01-23","cat":"finance"},
    {"name":"Australian Open",              "start":"2026-01-19","end":"2026-02-01","cat":"tennis"},
    {"name":"Haute Couture SS",             "start":"2026-01-26","end":"2026-01-30","cat":"fashion"},
    # February
    {"name":"Super Bowl LX",                "start":"2026-02-01","end":"2026-02-01","cat":"football"},
    {"name":"Six Nations Rugby",            "start":"2026-02-07","end":"2026-03-21","cat":"rugby"},
    {"name":"NY Fashion Week FW",           "start":"2026-02-06","end":"2026-02-11","cat":"fashion"},
    {"name":"London Fashion Week FW",       "start":"2026-02-13","end":"2026-02-17","cat":"fashion"},
    {"name":"Berlinale",                    "start":"2026-02-12","end":"2026-02-22","cat":"culture"},
    {"name":"Milan Fashion Week FW",        "start":"2026-02-17","end":"2026-02-23","cat":"fashion"},
    {"name":"MWC Barcelona",               "start":"2026-02-23","end":"2026-02-26","cat":"tech"},
    # March
    {"name":"Paris Fashion Week FW",        "start":"2026-02-24","end":"2026-03-03","cat":"fashion"},
    {"name":"F1 Season 2026",               "start":"2026-03-15","end":"2026-11-29","cat":"f1"},
    # April
    {"name":"Art Paris",                    "start":"2026-04-02","end":"2026-04-05","cat":"culture"},
    {"name":"Grand National",               "start":"2026-04-04","end":"2026-04-04","cat":"horses"},
    {"name":"The Masters",                  "start":"2026-04-09","end":"2026-04-12","cat":"golf"},
    {"name":"Coachella",                    "start":"2026-04-10","end":"2026-04-19","cat":"music"},
    {"name":"Venice Biennale",              "start":"2026-04-18","end":"2026-11-22","cat":"culture"},
    # May
    {"name":"Met Gala",                     "start":"2026-05-04","end":"2026-05-04","cat":"fashion"},
    {"name":"Frieze New York",              "start":"2026-05-07","end":"2026-05-11","cat":"culture"},
    {"name":"European Aquatics Champs",     "start":"2026-05-11","end":"2026-05-17","cat":"swimming"},
    {"name":"Cannes Film Festival",         "start":"2026-05-12","end":"2026-05-23","cat":"culture"},
    {"name":"UEFA Europa League Final",     "start":"2026-05-20","end":"2026-05-20","cat":"football"},
    {"name":"F1 Monaco GP",                 "start":"2026-05-24","end":"2026-05-24","cat":"f1"},
    {"name":"Roland Garros",                "start":"2026-05-25","end":"2026-06-07","cat":"tennis"},
    {"name":"UEFA Champions League Final",  "start":"2026-05-30","end":"2026-05-30","cat":"football"},
    # June
    {"name":"Epsom Derby",                  "start":"2026-06-06","end":"2026-06-06","cat":"horses"},
    {"name":"Prix du Jockey Club",          "start":"2026-06-07","end":"2026-06-07","cat":"horses"},
    {"name":"FIFA World Cup 2026",          "start":"2026-06-11","end":"2026-07-19","cat":"football"},
    {"name":"Vivatech Paris",               "start":"2026-06-11","end":"2026-06-14","cat":"tech"},
    {"name":"G7 Summit",                    "start":"2026-06-13","end":"2026-06-15","cat":"finance"},
    {"name":"Royal Ascot",                  "start":"2026-06-16","end":"2026-06-20","cat":"horses"},
    {"name":"Art Basel Basel",              "start":"2026-06-17","end":"2026-06-21","cat":"culture"},
    {"name":"US Open Golf",                 "start":"2026-06-18","end":"2026-06-21","cat":"golf"},
    {"name":"Paris Men's Fashion Week",     "start":"2026-06-23","end":"2026-06-28","cat":"fashion"},
    {"name":"Glastonbury",                  "start":"2026-06-24","end":"2026-06-28","cat":"music"},
    {"name":"Wimbledon",                    "start":"2026-06-29","end":"2026-07-12","cat":"tennis"},
    {"name":"Henley Royal Regatta",         "start":"2026-06-30","end":"2026-07-04","cat":"rowing"},
    # July
    {"name":"Tour de France",               "start":"2026-07-04","end":"2026-07-26","cat":"cycling"},
    {"name":"F1 British GP (Silverstone)",  "start":"2026-07-05","end":"2026-07-05","cat":"f1"},
    {"name":"Haute Couture FW",             "start":"2026-07-06","end":"2026-07-10","cat":"fashion"},
    {"name":"The Open Championship",        "start":"2026-07-16","end":"2026-07-19","cat":"golf"},
    {"name":"World Aquatics Champs",        "start":"2026-07-17","end":"2026-08-02","cat":"swimming"},
    # August
    {"name":"Rolex Fastnet Race",           "start":"2026-08-09","end":"2026-08-16","cat":"sailing"},
    {"name":"US Open Tennis",               "start":"2026-08-31","end":"2026-09-13","cat":"tennis"},
    {"name":"Venice Film Festival",         "start":"2026-08-26","end":"2026-09-05","cat":"culture"},
    # September
    {"name":"F1 Italian GP (Monza)",        "start":"2026-09-06","end":"2026-09-06","cat":"f1"},
    {"name":"World Rowing Champs",          "start":"2026-09-06","end":"2026-09-13","cat":"rowing"},
    {"name":"TIFF Toronto",                 "start":"2026-09-10","end":"2026-09-20","cat":"culture"},
    {"name":"NY Fashion Week SS",           "start":"2026-09-05","end":"2026-09-11","cat":"fashion"},
    {"name":"London Fashion Week SS",       "start":"2026-09-12","end":"2026-09-16","cat":"fashion"},
    {"name":"UN General Assembly",          "start":"2026-09-15","end":"2026-09-25","cat":"finance"},
    {"name":"Milan Fashion Week SS",        "start":"2026-09-16","end":"2026-09-22","cat":"fashion"},
    # October
    {"name":"Paris Fashion Week SS",        "start":"2026-09-28","end":"2026-10-06","cat":"fashion"},
    {"name":"Prix de l'Arc de Triomphe",    "start":"2026-10-04","end":"2026-10-04","cat":"horses"},
    {"name":"Frieze London",                "start":"2026-10-14","end":"2026-10-18","cat":"culture"},
    # November
    {"name":"Web Summit",                   "start":"2026-11-02","end":"2026-11-05","cat":"tech"},
    {"name":"Paris Photo",                  "start":"2026-11-12","end":"2026-11-15","cat":"culture"},
    {"name":"G20 Summit",                   "start":"2026-11-18","end":"2026-11-19","cat":"finance"},
    {"name":"F1 Abu Dhabi GP",              "start":"2026-11-29","end":"2026-11-29","cat":"f1"},
    # December
    {"name":"Art Basel Miami",              "start":"2026-12-04","end":"2026-12-06","cat":"culture"},

    # ══ 2027 ══════════════════════════════════════════════════════════════════
    # January
    {"name":"CES Las Vegas 2027",           "start":"2027-01-05","end":"2027-01-08","cat":"tech"},
    {"name":"Davos / WEF 2027",             "start":"2027-01-18","end":"2027-01-22","cat":"finance"},
    {"name":"Australian Open 2027",         "start":"2027-01-18","end":"2027-02-01","cat":"tennis"},
    {"name":"Haute Couture SS 2027",        "start":"2027-01-25","end":"2027-01-29","cat":"fashion"},
    # February
    {"name":"Six Nations Rugby 2027",       "start":"2027-02-06","end":"2027-03-20","cat":"rugby"},
    {"name":"NY Fashion Week FW 2027",      "start":"2027-02-05","end":"2027-02-10","cat":"fashion"},
    {"name":"Berlinale 2027",               "start":"2027-02-11","end":"2027-02-21","cat":"culture"},
    {"name":"London Fashion Week FW 2027",  "start":"2027-02-12","end":"2027-02-16","cat":"fashion"},
    {"name":"Milan Fashion Week FW 2027",   "start":"2027-02-16","end":"2027-02-22","cat":"fashion"},
    {"name":"MWC Barcelona 2027",           "start":"2027-02-22","end":"2027-02-25","cat":"tech"},
    # March
    {"name":"Paris Fashion Week FW 2027",   "start":"2027-02-23","end":"2027-03-02","cat":"fashion"},
    # April
    {"name":"Art Paris 2027",               "start":"2027-04-01","end":"2027-04-04","cat":"culture"},
    {"name":"Grand National 2027",          "start":"2027-04-03","end":"2027-04-03","cat":"horses"},
    {"name":"The Masters 2027",             "start":"2027-04-08","end":"2027-04-11","cat":"golf"},
    {"name":"Coachella 2027",               "start":"2027-04-09","end":"2027-04-18","cat":"music"},
    # May
    {"name":"Met Gala 2027",                "start":"2027-05-03","end":"2027-05-03","cat":"fashion"},
    {"name":"Cannes Film Festival 2027",    "start":"2027-05-11","end":"2027-05-22","cat":"culture"},
    {"name":"F1 Monaco GP 2027",            "start":"2027-05-23","end":"2027-05-23","cat":"f1"},
    {"name":"Roland Garros 2027",           "start":"2027-05-24","end":"2027-06-06","cat":"tennis"},
    # June
    {"name":"Art Basel Basel 2027",         "start":"2027-06-16","end":"2027-06-20","cat":"culture"},
    {"name":"Glastonbury 2027",             "start":"2027-06-23","end":"2027-06-27","cat":"music"},
    {"name":"Wimbledon 2027",               "start":"2027-06-28","end":"2027-07-11","cat":"tennis"},
    # July
    {"name":"Tour de France 2027",          "start":"2027-07-03","end":"2027-07-25","cat":"cycling"},
    {"name":"Haute Couture FW 2027",        "start":"2027-07-05","end":"2027-07-09","cat":"fashion"},
    # August
    {"name":"Venice Film Festival 2027",    "start":"2027-08-25","end":"2027-09-04","cat":"culture"},
    # September
    {"name":"Rugby World Cup 2027",         "start":"2027-09-06","end":"2027-10-23","cat":"rugby"},
    {"name":"TIFF 2027",                    "start":"2027-09-09","end":"2027-09-19","cat":"culture"},
    {"name":"Ryder Cup 2027",               "start":"2027-09-24","end":"2027-09-26","cat":"golf"},
    # October
    {"name":"Prix de l'Arc 2027",           "start":"2027-10-03","end":"2027-10-03","cat":"horses"},
    {"name":"Frieze London 2027",           "start":"2027-10-13","end":"2027-10-17","cat":"culture"},
    # November
    {"name":"Paris Photo 2027",             "start":"2027-11-11","end":"2027-11-14","cat":"culture"},
    # December
    {"name":"Art Basel Miami 2027",         "start":"2027-12-03","end":"2027-12-05","cat":"culture"},
]
# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def _s(t):
    return html_lib.escape(str(t or ""), quote=True)
def _parse_date(entry):
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None
def _ts(dt):
    return int(dt.timestamp()) if dt else 0
def _ago(dt):
    if not dt:
        return ""
    diff = int((datetime.now(timezone.utc) - dt).total_seconds())
    if diff < 3600:  return f"{diff//60}m"
    if diff < 86400: return f"{diff//3600}h"
    return f"{diff//86400}d"
def _img(entry):
    mt = getattr(entry, "media_thumbnail", None)
    if mt and isinstance(mt, list) and mt[0].get("url"):
        return mt[0]["url"]
    for mc in getattr(entry, "media_content", []):
        url = mc.get("url", "")
        if url and ("image" in mc.get("type","") or
                    url.lower().endswith((".jpg",".jpeg",".png",".webp"))):
            return url
    for enc in getattr(entry, "enclosures", []):
        if "image" in enc.get("type","") and enc.get("href"):
            return enc["href"]
    for field in [entry.get("summary",""),
                  (entry.get("content") or [{}])[0].get("value","")]:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', field or "")
        if m and m.group(1).startswith("http"):
            return m.group(1)
    return ""
def _filter_recent(arts, days=2, weekly_days=7):
    """Keep articles from last `days` days. Weekly newsletter sources get `weekly_days`."""
    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff_daily  = now_ts - days * 86400
    cutoff_weekly = now_ts - weekly_days * 86400
    result = []
    for a in arts:
        if not a["ts"]:          # no date → keep
            result.append(a)
        elif a["source"] in WEEKLY_SOURCES:
            if a["ts"] >= cutoff_weekly:
                result.append(a)
        else:
            if a["ts"] >= cutoff_daily:
                result.append(a)
    return result
def _filter_keywords(arts, keywords):
    """Keep only articles whose title+snippet contains at least one keyword (case-insensitive)."""
    pats = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
    def _matches(a):
        text = (a.get("title","") or "") + " " + (a.get("snip","") or "")
        return any(p.search(text) for p in pats)
    return [a for a in arts if _matches(a)]

def _filter_city_local(arts):
    """Keep only articles whose title mentions the city they're attributed to.
    Curated city-section sources (Le Monde Marseille/Paris, Marsactu) pass through
    untouched — they're editorially scoped to the city already.
    Other sources require the city name in the title.
    """
    # These sources are editorially scoped — trust them entirely
    TRUSTED_LOCAL = {"Marsactu", "Le Monde Marseille", "Le Monde Paris"}
    MARSEILLE_PATS = [re.compile(t, re.IGNORECASE) for t in [
        r"marseille", r"marseillais", r"bouches.du.rh",
    ]]
    PARIS_PATS = [re.compile(t, re.IGNORECASE) for t in [
        r"paris", r"parisien", r"parisienne", r"arrondissement",
        r"île.de.france", r"seine.saint.denis", r"val.de.marne",
        r"hauts.de.seine",
    ]]
    result = []
    for a in arts:
        src = a.get("source", "")
        title = a.get("title", "") or ""
        if src in TRUSTED_LOCAL:
            result.append(a)
        elif src in MARSEILLE_SOURCE_NAMES:
            if any(p.search(title) for p in MARSEILLE_PATS):
                result.append(a)
        else:  # Paris sources
            if any(p.search(title) for p in PARIS_PATS):
                result.append(a)
    return result
def _snip(entry):
    raw = entry.get("summary","") or ""
    txt = re.sub(r"<[^>]+>"," ", raw)
    txt = re.sub(r"\s+"," ", txt).strip()
    return txt[:280] + "…" if len(txt) > 280 else txt
# Strip source name suffixes that Google News RSS embeds in article titles
# e.g. "Italy extends tax cuts – Les Echos" → "Italy extends tax cuts"
_TITLE_SUFFIX_RE = re.compile(
    r'\s*[\-–|]\s*(?:Investir Les Echos|Les Echos|Le Monde|Le Parisien|L\'[ÉE]quipe|Télérama|Telerama|'
    r'Financial Times|The Economist|Reuters|BBC(?:\s+\w+)?|Al Jazeera|'
    r'The New York Times|Defense News|NSS Magazine|The NYT Arts|'
    r'Timeout(?:\s+\w+)?|The NBS|Silicon(?:Mania|Carne)|TBPN|'
    r'FT(?:\s+\w+)?|AFP|AP News|Politico|Bloomberg)\s*$',
    re.IGNORECASE
)
def _clean_title(t):
    return _TITLE_SUFFIX_RE.sub('', (t or '—').strip()).strip()

def _fetch(sources):
    arts = []
    for name, url in sources:
        try:
            feed = feedparser.parse(url,
                agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                request_headers={"Accept":"application/rss+xml,application/xml,text/xml,*/*"})
            for e in feed.entries[:MAX_PER_SOURCE]:
                arts.append({
                    "source":  name,
                    "title":   _clean_title(e.get("title","—")),
                    "link":    e.get("link","#"),
                    "date":    _parse_date(e),
                    "ts":      _ts(_parse_date(e)),
                    "img":     _img(e),
                    "snip":    _snip(e),
                })
        except Exception as ex:
            print(f"  ⚠  {name}: {ex}")
    arts.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return arts
def _dedup_exact(arts):
    """Collapse articles with identical titles from the same canonical publication.
    e.g. FT Tech + FT Companies Tech publishing the same article → keep one."""
    seen = {}   # (canonical_source, normalised_title) → index kept
    result = []
    for a in arts:
        canon  = SOURCE_CANONICAL.get(a["source"], a["source"])
        key    = (canon, re.sub(r"\s+", " ", a["title"].strip().lower()))
        if key not in seen:
            seen[key] = True
            # Store canonical name so grouping later sees it correctly
            a = dict(a); a["_canon"] = canon
            result.append(a)
    return result

def _entities(title):
    """Extract proper nouns / brand names — language-agnostic (OpenAI = OpenAI in FR and EN)."""
    # Capitalised words 5+ chars, excluding sentence-start stop words
    ECAP_STOP = {"The","This","That","These","Those","With","From","After",
                 "Before","About","Under","While","Their","There","Where","When",
                 "Pour","Dans","Avec","Sous","Mais","Plus","Tout","Sans","Vers",
                 "Entre","Contre","Selon"}
    caps = {w for w in re.findall(r'\b[A-Z][A-Za-z]{4,}\b', title) if w not in ECAP_STOP}
    # All-caps acronyms 2-6 chars (Fed, ECB, NATO, AI...)
    acro = set(re.findall(r'\b[A-Z]{2,6}\b', title))
    return caps | acro

def _dedup(arts):
    STOP = {"the","a","an","in","of","to","and","for","on","at","is","are","was",
            "with","by","that","this","as","it","its","from","be","or","has","have",
            "had","will","than","after","but","not","about","new","de","la","le",
            "les","du","un","une","en","et","des","sur","pour","par","dans","est"}
    def words(t):
        return {w for w in re.sub(r"[^a-z0-9àâéèêëîïôùûü ]","",t.lower()).split()
                if w not in STOP and len(w)>2}
    groups, used = [], set()
    for i, a in enumerate(arts):
        if i in used: continue
        wi = words(a["title"])
        ei = _entities(a["title"])
        grp = [a]
        for j, b in enumerate(arts):
            if j<=i or j in used: continue
            wj = words(b["title"])
            ej = _entities(b["title"])
            u  = wi | wj
            # Same-language: word overlap ≥ 25%
            word_match   = bool(u) and len(wi & wj)/len(u) >= 0.25
            # Cross-language: share 2+ proper nouns/acronyms (1 is too broad — e.g. "Italy")
            shared_ent   = ei & ej
            entity_match = (
                len(shared_ent) >= 2                          # two entities match
                or any(len(e) >= 8 for e in shared_ent)      # or one very long brand name
            )
            if word_match or entity_match:
                grp.append(b); used.add(j)
        used.add(i); groups.append(grp)
    return groups

def _sort_groups(groups):
    """Float multi-source groups to the top, both halves sorted by recency."""
    multi  = [g for g in groups if len(g) > 1]
    single = [g for g in groups if len(g) == 1]
    return multi + single
def _match_conflicts(arts):
    """Return {conflict_id: [article_dict, ...]}"""
    out = {c["id"]: [] for c in CONFLICTS}
    for a in arts:
        txt = (a["title"]+" "+a.get("snip","")).lower()
        for c in CONFLICTS:
            if any(kw in txt for kw in c["keywords"]) and len(out[c["id"]]) < 10:
                out[c["id"]].append(a)
    return out
def _route_afp(msgs):
    """Sort AFP messages into tech, macro, conflict pools, and a ticker remainder."""
    tech, macro, ticker = [], [], []
    conflict_pool = []
    for m in msgs:
        txt = (m["title"]+" "+m.get("snip","")).lower()
        matched_conflict = any(
            any(kw in txt for kw in c["keywords"])
            for c in CONFLICTS
        )
        if matched_conflict:
            conflict_pool.append(m)
        elif any(kw in txt for kw in TECH_KEYWORDS):
            tech.append(m)
        elif any(kw in txt for kw in MACRO_KEYWORDS):
            macro.append(m)
        else:
            ticker.append(m)
    return {"conflict": conflict_pool, "tech": tech,
            "macro": macro, "ticker": ticker}
def _fetch_telegram():
    if not CONFIG_FILE.exists() or not SESSION_FILE.with_suffix(".session").exists():
        print("  ⚠  Telegram: no session — skipping (run telegram_auth.py)")
        return []
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        print("  ⚠  Telegram: telethon not installed — skipping")
        return []
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except Exception as e:
        print(f"  ⚠  Telegram config: {e}")
        return []
    arts = []
    try:
        with TelegramClient(str(SESSION_FILE), cfg["api_id"], cfg["api_hash"]) as client:
            for label, channel in TELEGRAM_CHANNELS:
                try:
                    entity = client.get_entity(channel)
                    msgs   = client.get_messages(entity, limit=TELEGRAM_LIMIT)
                    is_inv = channel.startswith("https://t.me/+")
                    for msg in msgs:
                        txt = (msg.text or "").strip()
                        if not txt or len(txt) < 20: continue
                        lines = txt.split("\n")
                        title = lines[0][:160]
                        snip  = " ".join(lines[1:])[:280] if len(lines)>1 else ""
                        link  = channel if is_inv else f"https://t.me/{channel}/{msg.id}"
                        dt    = msg.date.replace(tzinfo=timezone.utc) if msg.date else None
                        arts.append({"source":label,"title":title,"link":link,
                                     "date":dt,"ts":_ts(dt),"img":"","snip":snip})
                except Exception as e:
                    print(f"  ⚠  Telegram {channel}: {e}")
    except Exception as e:
        print(f"  ⚠  Telegram client: {e}")
    arts.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    print(f"    → {len(arts)} AFP messages")
    return arts
_TRUSTED_PUBS = frozenset([
    "financial times","ft","economist","les echos","le monde","reuters","bbc",
    "al jazeera","new york times","nyt","nss magazine","art newspaper",
    "télérama","telerama","l'équipe","l'equipe","equipe","defense news",
    "timeout","the liber","theliber","the free press","thefp",
    "silicon carne","not boring","tbpn","the nbs","siliconmania","silicon mania",
    "sortir","leparisien","parisien",
])

def _fetch_event_news(name, max_items=8):
    """Fetch latest news for a calendar event, filtered to trusted sources."""
    q = name.replace(" ", "+").replace("'", "").replace("&", "and")
    url = (f"https://news.google.com/rss/search?q=%22{q}%22"
           f"&hl=en&gl=US&ceid=US:en")
    try:
        feed = feedparser.parse(
            url,
            agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            request_headers={"Accept": "application/rss+xml,application/xml,text/xml,*/*"},
        )
        def _make(e):
            dt  = _parse_date(e)
            src = getattr(getattr(e, "source", None), "title", "") or ""
            return {"title": e.get("title", "—"), "link": e.get("link", "#"),
                    "source": src, "ago": _ago(dt), "img": _img(e)}
        # First pass: trusted publishers only
        arts = []
        for e in feed.entries[:30]:
            src = (getattr(getattr(e, "source", None), "title", "") or "").lower()
            if any(p in src for p in _TRUSTED_PUBS):
                arts.append(_make(e))
            if len(arts) >= max_items:
                break
        # Fallback: too few trusted results → take top unfiltered
        if len(arts) < 3:
            arts = [_make(e) for e in feed.entries[:max_items]]
        return arts
    except Exception as ex:
        print(f"  ⚠  event news ({name}): {ex}")
        return []

def _fetch_calendar_event_news():
    """Fetch news for calendar events active within a ±30/+90 day window."""
    from datetime import timedelta
    today   = datetime.now()
    w_start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    w_end   = (today + timedelta(days=90)).strftime("%Y-%m-%d")
    relevant = [e for e in CALENDAR_EVENTS
                if e["start"] <= w_end and e["end"] >= w_start]
    out = {}
    for e in relevant:
        print(f"    → event news: {e['name']}")
        arts = _fetch_event_news(e["name"])
        if arts:
            out[e["name"]] = arts
    return out

# ══════════════════════════════════════════════════════════════════════════════
#  LES ECHOS — Python-side keyword filtering (reliable, no URL query parsing issues)
# ══════════════════════════════════════════════════════════════════════════════
_LE_FEED = "https://news.google.com/rss/search?q=site:lesechos.fr+-site:investir.lesechos.fr&hl=fr&gl=FR&ceid=FR:fr"

LES_ECHOS_TECH_KW = [
    "start-up","scale-up","licorne","décacorne","jeune pousse",
    "deeptech","fintech","biotech","cleantech","greentech","healthtech",
    "edtech","proptech","insurtech","legaltech","medtech","agritech",
    "adtech","martech","femtech","crypto","levée de fonds","lève",
    "série a","série b","série c","seed","amorçage","pré-seed",
    "tour de table","monte au capital","capital-risque","venture capital",
    "business angel","valorisation","liquidation judiciaire","acquisition",
    "incubateur","french tech","station f","bpifrance","atomico","eqt",
    "partech","kima","eurazeo","astanor","ledger","fondateur","cofondateur",
    "saas","data center","quantique","semi-conducteurs","souveraineté numérique",
    "intelligence artificielle","ia générative","ia française","machine learning",
    "openai","mistral","anthropic","deepmind","gafam","nvidia","llm","chatbot",
    "cybersécurité","robotique","high tech","elevenlab","lovable",
]

LES_ECHOS_MACRO_KW = [
    "bourse","marchés financiers","taux d'intérêt","bce","fed ","inflation",
    "pib","récession","cac 40","banque centrale","déficit budgétaire",
    "dette publique","matières premières","taux directeur","obligataire",
    "wall street","dow jones","s&p 500","euro stoxx","taux de change",
    "croissance économique","chômage","balance commerciale","politique monétaire",
    "obligations","spread","rendement","indice boursier","banque de france",
]

def _fetch_les_echos(keywords, label):
    """Fetch Les Echos broadly, filter by keywords in Python — 100% reliable."""
    arts = _fetch([(label, _LE_FEED)])
    result = []
    for a in arts:
        text = (a.get("title","") + " " + a.get("snip","")).lower()
        if any(kw.lower() in text for kw in keywords):
            result.append(a)
    print(f"    → Les Echos {label}: {len(result)}/{len(arts)} articles matched")
    return result

# Sources capped at 1 article (show only latest)
SOURCE_CAPS = {
    "Silicon Carne":     1,
    "Not Boring":        1,
    "TBPN":              1,
    "MTS Newsletter":    1,
    "Lenny's Newsletter":1,
    "Pragmatic Engineer":1,
    "The NBS":           1,
    "SiliconMania":      1,
    "First Round Review":1,
    "NSS Magazine":      1,
}
DEFAULT_CAP = 6  # all other sources

def _cap_per_source(arts):
    """Apply per-source caps — specific sources show only their latest article."""
    counts = {}
    result = []
    for a in arts:
        src = a["source"]
        cap = SOURCE_CAPS.get(src, DEFAULT_CAP)
        if counts.get(src, 0) < cap:
            result.append(a)
            counts[src] = counts.get(src, 0) + 1
    return result

# ══════════════════════════════════════════════════════════════════════════════
#  CSS
# ══════════════════════════════════════════════════════════════════════════════
CSS = """
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#ffffff;--bg2:#f2f2f7;--bg3:#D6E4F7;
  --border:#d1d1d6;--text:#0C0C0C;--muted:#5A7EA8;--dim:#8AAACE;
  --accent:#D42B17;--r:8px;
  --serif:'Cormorant Garamond',Georgia,serif;
  --sans:'DM Sans',-apple-system,BlinkMacSystemFont,sans-serif;
  --display:-apple-system,BlinkMacSystemFont,'SF Pro Display','SF Pro Text',sans-serif;
}
@media(prefers-color-scheme:dark){
  :root{--bg:#060606;--bg2:#0d0d0d;--bg3:#131313;
    --border:#1c1c1c;--text:#d4d4d4;--muted:#555;--dim:#333;--accent:#E84040}
  header{background:rgba(6,6,6,.97)}
  .cp-sum{color:#555}
  .cp-art{color:#555}
  .cp-art:hover{color:#bbb}
}
body{font-family:var(--sans);background:var(--bg);color:var(--text);
  font-size:13px;line-height:1.6;-webkit-font-smoothing:antialiased}

/* ── Header ──────────────────────────────────────────────────── */
header{display:flex;flex-direction:column;padding:0 60px;
  border-top:3px solid var(--text);border-bottom:1px solid var(--border);
  position:sticky;top:0;z-index:200;
  background:rgba(255,255,255,.96);backdrop-filter:blur(24px)}
@media(prefers-color-scheme:dark){header{background:rgba(6,6,6,.96)}}
.hd-inner{display:flex;justify-content:space-between;align-items:center;
  padding:16px 0 14px}
.hd-left{display:flex;flex-direction:column}
.hd-label{font-size:8px;letter-spacing:3.5px;text-transform:uppercase;
  color:var(--muted);margin-bottom:4px;font-family:var(--sans)}
header h1{font-family:var(--serif);font-size:34px;font-weight:600;
  font-style:italic;color:var(--text);letter-spacing:-1px;line-height:1}
.hd-right{display:flex;align-items:center;gap:20px}
.ts{font-size:9px;color:var(--muted);letter-spacing:.8px;text-transform:uppercase}
.ts-count{font-size:9px;color:var(--accent);letter-spacing:.5px;font-weight:600;
  white-space:nowrap;text-transform:uppercase}
.btn{background:transparent;color:var(--text);
  border:1px solid var(--border);
  font-size:9px;padding:7px 18px;border-radius:0;cursor:pointer;
  font-family:var(--sans);font-weight:700;letter-spacing:1.5px;
  text-transform:uppercase;transition:all .15s}
.btn:hover{background:var(--text);color:var(--bg);border-color:var(--text)}

/* ── Filter buttons (city tab bar) ──────────────────────────────── */
.fb{background:none;border:1px solid var(--border);color:var(--muted);
  font-size:8px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
  padding:4px 12px;border-radius:20px;cursor:pointer;
  font-family:var(--sans);transition:all .15s;flex-shrink:0}
.fb.on{background:var(--text);color:var(--bg2);border-color:var(--text)}
.fb:hover:not(.on){color:var(--text);border-color:var(--text)}

/* ── AFP Ticker ───────────────────────────────────────────────── */
.ticker{display:flex;align-items:stretch;gap:0;padding:0;
  border-bottom:1px solid var(--border);background:var(--bg);
  overflow:hidden;min-height:36px}
.ticker-label{font-size:8.5px;font-weight:700;letter-spacing:2px;
  text-transform:uppercase;color:#fff;background:var(--accent);
  padding:0 18px;display:flex;align-items:center;flex-shrink:0;z-index:2}
.ticker-track{flex:1;overflow:hidden;position:relative}
.ticker-items{display:flex;width:max-content;
  animation:ticker-scroll 55s linear infinite}
.ticker:hover .ticker-items{animation-play-state:paused}
@keyframes ticker-scroll{
  0%{transform:translateX(0)}
  100%{transform:translateX(-50%)}
}
.t-item{font-size:11.5px;color:var(--muted);text-decoration:none;
  padding:0 28px;border-right:1px solid var(--border);
  white-space:nowrap;transition:color .12s;
  display:flex;align-items:center;height:36px;flex-shrink:0}
.t-item:hover{color:var(--text)}

/* ── Section ─────────────────────────────────────────────────── */
.section{border-bottom:none}
.sec-hd{padding:0 60px;border-bottom:none;background:var(--bg);
  display:flex;align-items:center;justify-content:space-between}
.dot{display:none}
.cp-item .dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex-shrink:0}
.sec-hd-text{font-family:var(--display);font-size:20px;font-style:normal;
  font-weight:600;color:var(--text);padding:22px 0 18px;letter-spacing:-.5px}
.sec-hd-meta{font-size:9px;color:var(--dim);letter-spacing:.3px}

/* ── Layouts ─────────────────────────────────────────────────── */
.two-col{display:grid;grid-template-columns:1fr 1fr;border-bottom:none}
.two-col>.section{border-bottom:none;border-right:none}
.three-col{display:grid;grid-template-columns:1fr 1fr 1fr;border-bottom:none}
.three-col>.section{border-bottom:none;border-right:none;
  height:400px;display:flex;flex-direction:column;overflow:hidden}
.three-col .story-list,.three-col .paris-list{flex:1;overflow-y:auto;max-height:none}

/* ── Map ─────────────────────────────────────────────────────── */
.map-wrap{display:flex;height:440px}
#map{flex:0 0 62%;height:100%;overflow:hidden}
.cp{flex:1;display:flex;flex-direction:column;
  border-left:none;background:var(--bg2);overflow:hidden}
.cp-hd{display:none}
.cp-list{flex:1;overflow-y:auto;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.cp-list::-webkit-scrollbar{width:2px}
/* accordion item */
.cp-item{border-bottom:1px solid var(--border)}
.cp-item-row{display:flex;align-items:center;gap:9px;padding:9px 16px;
  cursor:pointer;transition:background .1s}
.cp-item-row:hover,.cp-item.open .cp-item-row{background:var(--bg3)}
.cp-item-name{font-size:12px;color:#999;flex:1;font-weight:300}
.cp-chevron{font-size:13px;color:#555;transition:transform .2s;flex-shrink:0}
.cp-item.open .cp-chevron{transform:rotate(90deg)}
.new-badge{font-size:9px;color:#F59E0B;flex-shrink:0}
/* expandable body */
.cp-item-body{display:none;padding:0 14px 14px 14px;border-top:1px solid #111}
.cp-item.open .cp-item-body{display:block}
.cp-meta{font-size:10px;color:var(--muted);margin-bottom:8px;letter-spacing:.2px;padding-top:10px}
.cp-sum{font-size:11px;color:#666;line-height:1.7;margin-bottom:10px;font-weight:300}
.cp-arts-hd{font-size:9px;font-weight:500;letter-spacing:1.2px;
  text-transform:uppercase;color:var(--dim);margin-bottom:7px}
.cp-art{display:block;padding:7px 0;border-bottom:1px solid var(--border);
  text-decoration:none;color:#555;font-size:11px;line-height:1.5;
  transition:color .12s;font-weight:300}
.cp-art:last-child{border-bottom:none}
.cp-art:hover{color:#ddd}
.cp-art small{color:var(--dim);font-size:9px}
.cp-no{font-size:11px;color:var(--dim);padding:8px 0}
@keyframes pulse-ring{
  0%{box-shadow:0 0 0 0 rgba(245,158,11,.65)}
  70%{box-shadow:0 0 0 10px rgba(245,158,11,0)}
  100%{box-shadow:0 0 0 0 rgba(245,158,11,0)}
}
.pulse-icon div{animation:pulse-ring 1.8s infinite;border-radius:50%}
.dark-popup .leaflet-popup-content-wrapper{
  background:#141414;color:#ddd;border:1px solid #252525;border-radius:10px}
.dark-popup .leaflet-popup-tip{background:#141414}

/* ── Story list (Tech / Sports / Cities) ─────────────────────── */
.story-list{padding:10px;max-height:560px;overflow-y:auto;
  margin:0 16px 16px;border-radius:20px;background:var(--bg2);
  display:flex;flex-direction:column;gap:5px;
  scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.story-list::-webkit-scrollbar{width:2px}
/* ── Story rows ──────────────────────────────────────────────── */
.sg{padding:12px 14px;border-bottom:none;background:var(--bg);border-radius:var(--r)}
.sg:not(.sg-multi){display:flex;align-items:baseline;gap:12px}
.sg-title{font-size:13px;color:var(--text);text-decoration:none;
  flex:1;line-height:1.45;transition:opacity .12s;font-weight:500;opacity:.72}
a.sg-title:hover{opacity:1}
.badge{font-size:7.5px;font-weight:600;color:var(--muted);flex-shrink:0;
  white-space:nowrap;letter-spacing:.9px;text-transform:uppercase;
  background:rgba(128,128,128,.15);border-radius:4px;padding:2px 7px}
.sg-time{font-size:9px;color:var(--dim);flex-shrink:0}
/* ── Multi-source expandable groups ─────────────────────────── */
.sg-multi{cursor:pointer}
.sg-multi:hover .sg-title,.sg-multi.open .sg-title{opacity:1}
.sg-hd{display:flex;align-items:baseline;gap:12px}
.sg-cnt{font-size:7.5px;font-weight:700;color:var(--accent);flex-shrink:0;
  letter-spacing:.9px;text-transform:uppercase}
.sg-arts{display:none;margin-top:10px;border-top:1px solid var(--border);padding-top:2px}
.sg-multi.open .sg-arts{display:flex;flex-direction:column}
.sg-art-link{display:flex;gap:14px;align-items:baseline;padding:8px 0;
  border-bottom:1px solid var(--border);text-decoration:none;color:var(--text)}
.sg-art-link:last-child{border-bottom:none}
.sg-art-src{font-size:7px;color:var(--dim);width:72px;flex-shrink:0;
  letter-spacing:.8px;text-transform:uppercase;font-weight:600;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sg-art-ttl{font-size:12.5px;color:var(--text);flex:1;line-height:1.5;
  font-weight:300;opacity:.68;transition:opacity .12s}
.sg-art-link:hover .sg-art-ttl{opacity:1;color:var(--accent)}
.sg-art-time{font-size:9px;color:var(--dim);flex-shrink:0}

/* ── Culture cards ───────────────────────────────────────────── */
.cards{display:flex;gap:1px;overflow-x:auto;padding:0 0 0;
  scrollbar-width:thin;scrollbar-color:var(--border) transparent;
  border-top:1px solid var(--border)}
.cards::-webkit-scrollbar{height:2px}
.cards::-webkit-scrollbar-thumb{background:var(--border)}
.card{flex:0 0 220px;background:var(--bg);border:none;
  border-right:1px solid var(--border);
  text-decoration:none;color:inherit;overflow:hidden;
  display:flex;flex-direction:column;transition:background .18s}
.card:hover{background:var(--bg2)}
.card:hover .ct{opacity:1}
.ci{height:148px;background-size:cover;background-position:center;
  position:relative;flex-shrink:0}
.ci::after{content:'';position:absolute;inset:0;
  background:linear-gradient(to top,rgba(0,0,0,.5) 0%,transparent 60%)}
.cs{position:absolute;bottom:9px;left:10px;z-index:1;
  font-size:7.5px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;
  color:rgba(255,255,255,.8)}
.cb{padding:12px 14px 14px;flex:1;display:flex;flex-direction:column;
  justify-content:space-between}
.ct{font-size:12px;line-height:1.5;color:var(--text);transition:opacity .15s;
  flex:1;margin-bottom:6px;font-weight:300;opacity:.72}
.ctime{font-size:9px;color:var(--dim)}

/* ── Paris / What's On ───────────────────────────────────────── */
.paris-list{padding:10px;max-height:560px;overflow-y:auto;
  margin:0 16px 16px;border-radius:20px;background:var(--bg2);
  display:flex;flex-direction:column;gap:5px;
  scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.paris-list::-webkit-scrollbar{width:2px}
.pi{display:flex;gap:12px;align-items:baseline;
  padding:12px 14px;border-bottom:none;background:var(--bg);border-radius:var(--r)}
.pi-src{font-size:7px;color:var(--muted);flex-shrink:0;
  white-space:nowrap;letter-spacing:.8px;text-transform:uppercase;font-weight:600;
  background:rgba(128,128,128,.15);border-radius:4px;padding:2px 6px}
.pi-title{font-size:13px;color:var(--text);text-decoration:none;
  flex:1;line-height:1.45;transition:opacity .12s;font-weight:500;opacity:.72}
.pi-title:hover{opacity:1}
.pi-t{font-size:9px;color:var(--dim);flex-shrink:0}

/* ── Calendar nav & event list ───────────────────────────────── */
.cal-btn{background:none;border:1px solid var(--border);color:var(--text);
  font-size:14px;width:30px;height:30px;border-radius:50%;cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  font-family:var(--sans);transition:all .15s}
.cal-btn:hover{background:var(--text);color:var(--bg2);border-color:var(--text)}
.cal-btn:disabled{opacity:.25;cursor:default;pointer-events:none}
.cal-nav-label{font-size:9px;font-weight:600;letter-spacing:1px;
  text-transform:uppercase;color:var(--muted)}
.cal-legend{display:flex;flex-wrap:wrap;gap:6px 16px;flex:1}
.cal-leg-i{display:flex;align-items:center;gap:5px;
  font-size:8px;font-weight:600;letter-spacing:.7px;
  text-transform:uppercase;color:var(--muted)}
.cal-leg-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.cal-months{display:grid;grid-template-columns:repeat(3,1fr);
  padding:24px 40px 40px;gap:0 48px;align-items:start}
.cal-month{min-width:0}
.cal-mhd{font-family:var(--serif);font-size:16px;font-style:italic;
  font-weight:600;color:var(--text);margin-bottom:12px;
  padding-bottom:9px;border-bottom:2px solid var(--border)}
.cal-elist{display:flex;flex-direction:column}
.cal-erow{display:grid;grid-template-columns:8px 1fr auto;
  align-items:start;gap:8px;padding:5px 0;border-bottom:1px solid var(--border)}
.cal-erow:last-child{border-bottom:none}
.cal-erow.ev-past{opacity:.33}
.cal-erow.ev-live{background:var(--bg3);border-radius:3px;
  padding:5px 7px;margin:0 -7px 1px -7px;border-bottom:none}
.cal-edot{width:7px;height:7px;border-radius:50%;flex-shrink:0;margin-top:3px}
.cal-ename{font-size:11.5px;color:var(--text);font-weight:300;line-height:1.4}
.cal-erange{font-size:9px;color:var(--muted);white-space:nowrap;
  text-align:right;padding-top:1px}
.cal-empty-msg{font-size:11px;color:var(--dim);padding:10px 0;font-style:italic}

/* ── Calendar event detail panel ────────────────────────────── */
.cal-det{border-top:2px solid var(--text);padding:36px 80px 64px;background:var(--bg2)}
.cal-det-hd{display:flex;align-items:center;justify-content:space-between;
  padding-bottom:20px;border-bottom:1px solid var(--border);margin-bottom:20px}
.cal-det-title{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.cal-det-name{font-family:var(--serif);font-size:18px;font-style:italic;
  font-weight:600;color:var(--text);text-decoration:none;
  border-bottom:1px solid transparent;transition:color .12s,border-color .12s}
.cal-det-name:hover{color:var(--accent);border-bottom-color:var(--accent)}
.cal-det-range{font-size:10px;color:var(--muted);letter-spacing:.3px}
.cal-det-close{background:none;border:1px solid var(--border);color:var(--muted);
  font-size:16px;width:28px;height:28px;border-radius:50%;cursor:pointer;
  display:flex;align-items:center;justify-content:center;flex-shrink:0;
  font-family:var(--sans);transition:all .15s}
.cal-det-close:hover{background:var(--text);color:var(--bg2);border-color:var(--text)}
.cal-det-arts{display:grid;grid-template-columns:repeat(2,1fr);gap:0 40px}
.cal-det-art{padding:11px 0;border-bottom:1px solid var(--border);
  text-decoration:none;color:var(--text);display:block;font-size:12.5px;
  line-height:1.6;font-weight:300;transition:color .12s}
.cal-det-art:hover{color:var(--accent)}
.cal-det-art small{display:block;font-size:9px;color:var(--muted);margin-top:3px;
  letter-spacing:.3px}
.cal-det-none{font-size:11px;color:var(--dim);padding:12px 0;font-style:italic}
.cal-erow[data-ev]{cursor:pointer}
.cal-erow[data-ev]:hover .cal-ename{color:var(--accent)}
.cal-erow.ev-sel .cal-ename{color:var(--accent)}
.cal-erow.ev-sel{background:var(--bg3);border-radius:3px;
  padding:5px 7px;margin:0 -7px 1px -7px;border-bottom:none}

/* ── Tablet (≤1100px) ────────────────────────────────────────── */
@media(max-width:1100px){
  .cal-months{grid-template-columns:repeat(2,1fr);gap:0 32px}
}

/* ── Mobile (≤768px) ─────────────────────────────────────────── */
@media(max-width:768px){
  /* header */
  header{padding:0 16px}
  .hd-inner{padding:10px 0 9px;flex-wrap:wrap;gap:6px}
  header h1{font-size:18px}
  .ts{font-size:8px;letter-spacing:.6px}
  .btn{padding:6px 12px;font-size:8px;margin-left:0}
  .hd-tabs{gap:0;overflow-x:auto;scrollbar-width:none}
  .hd-tabs::-webkit-scrollbar{display:none}
  .hd-tab{padding:8px 14px;font-size:8px;white-space:nowrap}

  /* section headers */
  .sec-hd{padding:0 16px}
  .sec-hd-text{font-size:16px;padding:16px 0 12px}

  /* story lists */
  .story-list{padding:0 16px 16px}
  .sg{padding:12px 0}
  .sg-title,.pi-title{font-size:12px}
  .sg-art-src{width:56px}
  .paris-list{padding:0 16px 16px}

  /* culture cards */
  .cards{padding:8px 16px 12px;gap:8px}
  .card{flex:0 0 calc(70vw)}

  /* grid overrides */
  .two-col{grid-template-columns:1fr}
  .two-col>.section{border-right:none;border-bottom:1px solid var(--border)}
  .two-col>.section:last-child{border-bottom:none}
  .three-col{grid-template-columns:1fr}
  .three-col>.section{height:auto;border-right:none;border-bottom:1px solid var(--border)}
  .three-col>.section:last-child{border-bottom:none}
  .three-col .story-list,.three-col .paris-list{max-height:260px}

  /* map */
  .map-wrap{flex-direction:column;height:auto}
  #map{flex:none;height:52vw;min-height:220px;width:100%}
  .cp{border-left:none;border-top:1px solid var(--border);height:260px;width:100%}
  .cp-hd{padding:10px 14px 8px;font-size:8px}
  .cp-item{padding:0}
  .cp-item-row{padding:8px 14px}

  /* calendar */
  .cal-months{grid-template-columns:1fr;padding:16px 16px 24px;gap:24px 0}
  .cal-legend{gap:5px 10px;padding:10px 16px}
  .cal-leg-i{font-size:7.5px}
  .cal-det{padding:20px 20px 32px}
  .cal-det-arts{grid-template-columns:1fr;gap:0}
  .cal-hd-tabs{overflow-x:auto;scrollbar-width:none;padding:0 16px}
  .cal-hd-tabs::-webkit-scrollbar{display:none}
}

/* ── Snap scroll layout ──────────────────────────────────── */
html{scroll-snap-type:y mandatory;overflow-y:scroll}
.snap-sec{height:100vh;scroll-snap-align:start;overflow:hidden;position:relative;clip-path:inset(0)}

/* ── Hero section ────────────────────────────────────────── */
.hero-sec{display:flex;flex-direction:column;justify-content:center;
  padding:0 60px;border-top:3px solid var(--text);background:var(--bg)}
.hero-eyebrow{font-size:8px;letter-spacing:3.5px;text-transform:uppercase;
  color:var(--muted);font-family:var(--sans);margin-bottom:16px;display:block}
.hero-h1{font-family:var(--display);font-size:clamp(60px,8.5vw,118px);
  font-style:normal;font-weight:700;color:var(--text);
  letter-spacing:-3px;line-height:.93;margin-bottom:36px}
.hero-meta{display:flex;align-items:center;gap:16px;margin-bottom:36px}
.hero-count{font-size:9px;color:var(--accent);letter-spacing:.9px;
  font-weight:600;text-transform:uppercase}
.hero-date-str{font-size:9px;color:var(--muted);letter-spacing:.9px;text-transform:uppercase}
.hero-sep{color:var(--dim);font-size:12px}
.hero-hint{position:absolute;bottom:90px;left:60px;font-size:7.5px;
  letter-spacing:2.5px;text-transform:uppercase;color:var(--dim)}
.hero-sec .ticker{position:absolute;bottom:0;left:0;right:0}

/* ── Snap section inner layouts ──────────────────────────── */
.snap-geo{height:100vh!important;overflow:hidden!important}
.snap-geo>.section{height:100%!important;display:flex!important;flex-direction:column!important}
.snap-geo .sec-hd{flex-shrink:0}
.snap-geo .map-wrap{flex:1!important;height:0!important;min-height:0!important}
.snap-geo #map{height:100%!important;position:relative!important}
.cp-list{position:relative}
.cp-list::after{content:'';position:sticky;bottom:0;display:block;
  height:40px;background:linear-gradient(to bottom,transparent,var(--bg2));
  pointer-events:none}
/* ── Map: light theme ─────────────────────────────────────── */
#map{background:var(--bg)!important}
#map .leaflet-control-zoom a{background:var(--bg)!important;color:var(--muted)!important;
  border-color:var(--border)!important}
#map .leaflet-control-zoom{border:none!important;box-shadow:none!important}
.snap-geo>.section{background:var(--bg)}
.snap-geo .sec-hd{background:var(--bg)!important;border-bottom:none;padding:0 16px}
.snap-geo .sec-hd-text{color:var(--text)}
.snap-geo .sec-hd-meta{color:var(--muted)}
.snap-geo .cp{background:var(--bg);border-left:none}
.snap-geo .cp-item{border-bottom-color:var(--border)}
.snap-geo .cp-item-name{color:var(--text)}
.snap-geo .cp-item-row:hover,.snap-geo .cp-item.open .cp-item-row{background:rgba(0,0,0,.04)}
.snap-geo .cp-chevron{color:var(--muted)}
.snap-geo .cp-item-body{border-top-color:var(--border)}
.snap-geo .cp-meta{color:var(--muted)}
.snap-geo .cp-sum{color:var(--text);opacity:.72}
.snap-geo .cp-arts-hd{color:var(--muted)}
.snap-geo .cp-art{color:var(--text);opacity:.72;border-bottom-color:var(--border)}
.snap-geo .cp-art:hover{opacity:1}
.snap-geo .cp-art small{color:var(--dim)}
.snap-geo .new-badge{color:#F59E0B}
.snap-geo .cp-list::after{display:none!important}
/* ── Conflict markers: match grid dot size (2×R = 5px) ──── */
.gm-dot{width:5px;height:5px;border-radius:50%;position:relative}
.gm-conflict{background:#EF4444}
.gm-tension{background:#EF4444}
/* blink: dot flashes when there's new coverage */
@keyframes dot-blink{
  0%,100%{opacity:1}
  50%{opacity:.4}
}
.gm-sonar{animation:dot-blink 1.2s ease-in-out infinite}

.snap-feed{display:flex;flex-direction:column;overflow:hidden;background:#FFB3C8}
.snap-feed>.two-col{flex:3 0 0;min-height:0;border-bottom:none}
.snap-feed .two-col>.section{height:100%;display:flex;flex-direction:column;
  overflow:hidden;border-bottom:none;background:#FFB3C8}
.snap-feed .sec-hd{background:#FFB3C8!important}
.snap-feed .poly-band{background:#FFB3C8!important}
/* ── Markets price band ──────────────────────────────────────── */
/* ── snap-feed bottom row: Polymarket (left) + Markets (right) ── */
.snap-feed-bottom{flex:1 0 0;min-height:0;display:flex;flex-direction:row;
  background:#FFB3C8;overflow:hidden}
.snap-feed-bottom .poly-band{flex:1;min-width:0;
  border-right:1px solid rgba(0,0,0,.07)}
.snap-feed-bottom .price-band{flex:1;min-width:0}
.price-band{display:flex;flex-direction:column;
  border-top:none;background:#FFB3C8;overflow:hidden}
.price-band .sec-hd{background:#FFB3C8!important;flex-shrink:0;padding-bottom:0;min-height:0}
.price-band-track{flex:1;overflow-x:auto;overflow-y:hidden;
  margin:0 16px 8px;border-radius:20px;background:var(--bg2);
  display:flex;flex-direction:row;align-items:stretch;
  gap:6px;padding:6px;scrollbar-width:none}
.price-band-track::-webkit-scrollbar{display:none}
.price-tile{flex:0 0 auto;min-width:90px;display:flex;flex-direction:column;
  justify-content:center;gap:3px;padding:6px 12px;
  border-radius:var(--r);background:var(--bg);cursor:default}
.price-tile-name{font-size:7.5px;font-weight:700;letter-spacing:1px;
  text-transform:uppercase;color:var(--muted)}
.price-tile-val{font-size:13px;font-weight:600;color:var(--text);font-variant-numeric:tabular-nums}
.price-tile-chg{font-size:9px;font-weight:600;font-variant-numeric:tabular-nums}
.price-tile-chg.up{color:#16A34A}
.price-tile-chg.dn{color:#DC2626}
.price-tile-loading{font-size:10px;color:var(--muted);padding:0 12px;align-self:center}
/* ── Polymarket band ─────────────────────────────────────────── */
.poly-band{display:flex;flex-direction:column;
  border-top:none;background:#FFB3C8;overflow:hidden}
.poly-band .sec-hd{background:#FFB3C8!important;flex-shrink:0;padding-bottom:0}
.poly-band-label{display:none}
.poly-band-track{flex:1;overflow:hidden;position:relative;
  margin:0 16px 8px;border-radius:20px;background:var(--bg2);padding:8px 0}
.poly-band-items{display:flex;height:100%;width:max-content;gap:8px;padding:0 8px;
  animation:ticker-scroll 60s linear infinite}
.poly-band:hover .poly-band-items{animation-play-state:paused}
/* card */
.poly-card{flex:0 0 220px;height:100%;display:flex;flex-direction:column;
  justify-content:center;gap:6px;
  padding:14px 16px;border-right:none;border-radius:var(--r);
  background:var(--bg);
  text-decoration:none;transition:transform .2s ease;cursor:pointer}
.poly-card:hover{transform:scale(1.04);z-index:2;position:relative}
.poly-card-q{font-size:12px;font-weight:600;color:var(--text);
  line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden}
.poly-card-outcomes{display:flex;flex-direction:column;gap:3px}
.poly-outcome{display:flex;align-items:center;gap:8px}
.poly-out-name{font-size:11px;color:var(--muted);
  flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.poly-out-pct{font-size:10.5px;font-weight:700;
  padding:2px 7px;border-radius:3px;flex-shrink:0}
.poly-out-pct.high{color:#16A34A;background:rgba(22,163,74,.12)}
.poly-out-pct.low{color:#DC2626;background:rgba(220,38,38,.12)}
.poly-card-vol{font-size:9px;color:var(--dim);letter-spacing:.3px;margin-top:2px}
.snap-feed .story-list{flex:1;max-height:none;overflow-y:auto;
  padding:10px;margin:0 16px 16px;border-radius:20px;background:var(--bg2);
  display:flex;flex-direction:column;gap:5px}
/* story rows — white card on grey container */
.snap-feed .sg{
  border-bottom:none;padding:12px 14px;margin:0;
  background:var(--bg);border-radius:var(--r);
  position:relative;overflow:visible}
.snap-feed .sg::before{
  content:'';position:absolute;inset:0;
  background:#fff;border-radius:var(--r);
  transform-origin:bottom center;
  transform:perspective(500px) rotateX(45deg);
  opacity:0;
  transition:transform .42s cubic-bezier(.22,1,.36,1),opacity .3s ease;
  z-index:0;pointer-events:none}
.snap-feed .sg:hover::before,
.snap-feed .sg-multi.open::before{
  transform:perspective(500px) rotateX(0deg);opacity:1}
.snap-feed .sg>*,.snap-feed .sg .sg-hd{position:relative;z-index:1}
.snap-feed .sg:hover .sg-title,
.snap-feed .sg-multi.open .sg-title{color:#111!important;opacity:1}
.snap-feed .sg:hover .sg-time,
.snap-feed .sg:hover .sg-cnt,
.snap-feed .sg-multi.open .sg-time,.snap-feed .sg-multi.open .sg-cnt{color:rgba(0,0,0,.4)}
.snap-feed .sg-arts{border-top:1px solid rgba(0,0,0,.1);margin-top:8px;padding-top:0}
.snap-feed .sg-art-link{border-bottom:1px solid rgba(0,0,0,.07)}

.snap-culture>.section{height:100%;display:flex;flex-direction:column;
  background:var(--bg)}
.snap-culture .sec-hd{background:var(--bg)!important;border-color:var(--border)!important}
.snap-culture .sec-hd-text{color:var(--text)!important}
/* culture body: flex column splits grid (3/4) vs event band (1/4) */
.snap-culture .culture-body{
  flex:1;min-height:0;
  display:flex;flex-direction:column}
/* culture: 3-row horizontal scroll — 3/4 of available height */
.snap-culture .cards{
  flex:3 0 0;min-height:0;
  display:grid!important;
  /* rows share the flex height equally */
  grid-template-rows:repeat(3,1fr);
  grid-auto-flow:column;
  /* column width ≈ row height → squares
     row height ≈ (100vh - 66px)*3/4 / 3 - gaps ≈ (100vh-66px)/4 - 15px */
  grid-auto-columns:calc((100vh - 66px) / 4 - 15px);
  gap:12px;padding:14px 20px 8px;
  overflow-x:auto;overflow-y:hidden;
  border-top:none!important;
  scrollbar-width:none}
.snap-culture .cards::-webkit-scrollbar{display:none}
.snap-culture .card{
  display:block!important;
  position:relative;
  width:100%;height:100%;
  border-right:none;
  border-radius:var(--r);overflow:hidden;
  cursor:pointer;
  transition:transform .2s ease,box-shadow .2s ease,z-index .2s ease;
  text-decoration:none}
.snap-culture .card:hover:not(.cv-open){
  transform:scale(1.05);
  z-index:2;
  box-shadow:0 10px 30px rgba(0,0,0,.45)}
/* image fills the whole card */
.snap-culture .ci{
  position:absolute!important;inset:0!important;
  height:100%!important;width:100%!important;flex-shrink:0}
.snap-culture .ci::after{
  background:linear-gradient(to top,rgba(0,0,0,.95) 0%,rgba(0,0,0,.65) 42%,rgba(0,0,0,.15) 68%,transparent 85%)}
/* source label — dark pill so it reads on any image */
.snap-culture .cs{
  top:9px;bottom:auto;z-index:3;
  background:rgba(0,0,0,.62);
  backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px);
  border-radius:4px;padding:3px 7px;
  color:#fff!important;font-size:8px;letter-spacing:1.2px}
/* normal text bottom */
.snap-culture .cb{
  position:absolute!important;bottom:0;left:0;right:0;
  padding:22px 11px 12px;background:none;
  display:flex;flex-direction:column;justify-content:flex-end;z-index:2;
  transition:opacity .2s}
.snap-culture .card.cv-open .cb{opacity:0;pointer-events:none}
.snap-culture .ct{
  color:#fff!important;opacity:1!important;
  font-size:18px;margin-bottom:4px;line-height:1.3;font-weight:500;
  text-shadow:0 1px 6px rgba(0,0,0,1),0 2px 10px rgba(0,0,0,.8)}
.snap-culture .ctime{color:rgba(255,255,255,.45);font-size:11px}
/* info overlay — slides up from bottom within card */
.snap-culture .cv-overlay{
  position:absolute;left:0;right:0;bottom:0;
  height:100%;z-index:6;
  background:rgba(0,0,0,.82);
  backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);
  padding:13px 12px 14px;
  display:flex;flex-direction:column;justify-content:flex-end;gap:5px;
  transform:translateY(100%);
  transition:transform .3s ease}
.snap-culture .card.cv-open .cv-overlay{transform:translateY(0)}
.snap-culture .cv-src{font-size:8px;font-weight:700;letter-spacing:1px;
  text-transform:uppercase;color:rgba(255,255,255,.45)}
.snap-culture .cv-title{font-size:12px;font-weight:500;color:#fff;line-height:1.4}
.snap-culture .cv-snip{font-size:10px;color:rgba(255,255,255,.65);line-height:1.45;
  overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}
.snap-culture .cv-footer{display:flex;align-items:center;justify-content:space-between;
  margin-top:6px}
.snap-culture .cv-time{font-size:8px;color:rgba(255,255,255,.38)}
.snap-culture .cv-read{
  font-size:10px;font-weight:600;color:#fff;
  background:rgba(255,255,255,.13);
  border:1px solid rgba(255,255,255,.2);
  border-radius:4px;padding:3px 9px;text-decoration:none;
  transition:background .15s}
.snap-culture .cv-read:hover{background:rgba(255,255,255,.25)}
/* ── culture event band (bottom 1/4 of culture section) ──── */
.snap-culture .culture-cal-band{
  flex:1 0 0;min-height:0;
  display:flex;flex-direction:row;align-items:stretch;
  overflow-x:auto;overflow-y:hidden;
  gap:8px;
  /* side padding = 50% − half expanded width (55%÷2=27.5%) so edge cards can reach centre */
  padding:8px 22.5% 10px;
  border-top:none;
  -webkit-overflow-scrolling:touch;scrollbar-width:none}
.snap-culture .culture-cal-band::-webkit-scrollbar{display:none}
.snap-culture .culture-cal-band .cal-ev-card{
  flex:0 0 12%;
  position:relative;border-radius:10px;overflow:hidden;
  cursor:pointer;opacity:.4;
  will-change:flex-basis,opacity;
  transition:flex-basis .38s cubic-bezier(.25,0,.1,1),opacity .3s ease,box-shadow .25s ease}
.snap-culture .culture-cal-band .cal-ev-card.ev-center{
  flex:0 0 55%;opacity:1;box-shadow:0 8px 32px rgba(0,0,0,.6)}
.snap-culture .culture-cal-band .cal-ev-card.ev-past{opacity:.2;cursor:default}
.snap-culture .culture-cal-band .cal-ev-card.ev-past.ev-center{opacity:.35}
.snap-culture .culture-cal-band .cal-ev-bg{
  position:absolute;inset:0;
  background:var(--bg2)}
.snap-culture .culture-cal-band .cal-ev-bg::after{display:none}
.snap-culture .culture-cal-band .cal-ev-card.ev-past .cal-ev-bg{filter:grayscale(.4);opacity:.6}
.snap-culture .culture-cal-band .cal-ev-body{
  position:absolute;bottom:0;left:0;right:0;
  padding:10px 12px 12px;
  background:none;
  transition:transform .35s ease}
.snap-culture .culture-cal-band .cal-ev-card.ev-open .cal-ev-body{transform:translateY(-6px)}
.snap-culture .culture-cal-band .cal-ev-meta{display:flex;align-items:center;gap:6px;margin-bottom:4px}
.snap-culture .culture-cal-band .cal-ev-cat-chip{
  font-size:8px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
  color:var(--muted);background:rgba(0,0,0,.07);
  border:1px solid rgba(0,0,0,.1);border-radius:4px;padding:2px 7px}
.snap-culture .culture-cal-band .cal-live-badge{
  font-size:8px;font-weight:700;letter-spacing:.5px;
  color:#fff;background:#16A34A;border-radius:4px;padding:2px 6px;
  animation:live-pulse 2s ease-in-out infinite}
.snap-culture .culture-cal-band .cal-ev-name{
  font-size:clamp(11px,1.2vw,17px);font-weight:600;
  color:var(--text);line-height:1.2;margin-bottom:3px;
  text-shadow:none}
.snap-culture .culture-cal-band .cal-ev-range{
  font-size:10px;color:var(--muted);font-weight:300}
.snap-culture .culture-cal-band .cal-ev-panel{
  position:absolute;left:0;right:0;bottom:0;height:65%;
  background:rgba(242,242,247,.96);
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  padding:10px 14px 12px;
  overflow-y:auto;scrollbar-width:none;
  transform:translateY(100%);transition:transform .38s ease}
.snap-culture .culture-cal-band .cal-ev-panel::-webkit-scrollbar{display:none}
.snap-culture .culture-cal-band .cal-ev-card.ev-open .cal-ev-panel{transform:translateY(0)}
.snap-culture .culture-cal-band .cal-ev-panel-hd{
  font-size:8px;font-weight:700;letter-spacing:1px;
  text-transform:uppercase;color:var(--muted);margin-bottom:8px}
.snap-culture .culture-cal-band .cal-det-art{
  display:flex;flex-direction:column;gap:2px;
  padding:7px 0;border-bottom:1px solid var(--border);text-decoration:none}
.snap-culture .culture-cal-band .cal-det-art:last-of-type{border-bottom:none}
.snap-culture .culture-cal-band .cal-det-art-title{font-size:11px;color:var(--text);line-height:1.35;font-weight:400}
.snap-culture .culture-cal-band .cal-det-art-meta{font-size:9px;color:var(--muted)}
.snap-culture .culture-cal-band .cal-det-none{font-size:10px;color:var(--muted);margin:3px 0}
.snap-culture .culture-cal-band .cal-search-link{
  display:inline-block;margin-top:8px;
  font-size:9px;font-weight:600;color:var(--muted);
  text-decoration:none;border-bottom:1px solid var(--border)}
.snap-culture .culture-cal-band .cal-search-link:hover{color:var(--text)}

.snap-bottom{background:#00A550}
.snap-bottom>.three-col{height:100%;border-bottom:none}
.snap-bottom .three-col>.section{height:100%!important;background:#00A550}
.snap-bottom .sec-hd{background:#00A550!important}
.snap-bottom .story-list,.snap-bottom .paris-list{max-height:none}

/* ── shared card token (used below) ─────────────────────────
   padding: 11px 12px  |  gap: 5px  |  radius: var(--r)
   ────────────────────────────────────────────────────────── */

/* ── snap-bottom: sport / cities / paris ─────────────────── */
.snap-bottom .story-list,.snap-bottom .paris-list{
  padding:10px;margin:0 16px 16px;border-radius:20px;background:var(--bg2);
  display:flex;flex-direction:column;gap:5px}
.snap-bottom .sg,.snap-bottom .pi{
  border-bottom:none;padding:12px 14px;margin:0;
  background:var(--bg);border-radius:var(--r);
  position:relative;overflow:visible}
.snap-bottom .sg::before,.snap-bottom .pi::before{
  content:'';position:absolute;inset:0;
  background:#fff;border-radius:var(--r);
  transform-origin:bottom center;
  transform:perspective(500px) rotateX(45deg);
  opacity:0;
  transition:transform .42s cubic-bezier(.22,1,.36,1),opacity .3s ease;
  z-index:0;pointer-events:none}
.snap-bottom .sg:hover::before,.snap-bottom .pi:hover::before,
.snap-bottom .sg-multi.open::before{
  transform:perspective(500px) rotateX(0deg);opacity:1}
.snap-bottom .sg>*,.snap-bottom .pi>*,.snap-bottom .sg .sg-hd{position:relative;z-index:1}
.snap-bottom .sg:hover .sg-title,.snap-bottom .sg-multi.open .sg-title,
.snap-bottom .pi:hover .pi-title{color:#111!important;opacity:1}
.snap-bottom .sg:hover .sg-time,
.snap-bottom .sg:hover .sg-cnt,
.snap-bottom .pi:hover .pi-t{color:rgba(0,0,0,.4)}
.snap-bottom .sg-arts{border-top:1px solid rgba(0,0,0,.1);margin-top:8px;padding-top:0}
.snap-bottom .sg-art-link{border-bottom:1px solid rgba(0,0,0,.07)}

/* ── snap-geo: conflict accordion items ──────────────────── */
.snap-geo .cp-list{padding:10px;margin:0 10px 10px;border-radius:20px;background:var(--bg2);
  display:flex;flex-direction:column;gap:5px}
.snap-geo .cp-item{
  border-bottom:none;
  background:var(--bg);border-radius:var(--r);overflow:hidden}
.snap-geo .cp-item.has-new{background:rgba(239,68,68,.08)}
.snap-geo .cp-item-row{
  position:relative;overflow:hidden;border-radius:var(--r)}
.snap-geo .cp-item-row::before{
  content:'';position:absolute;inset:0;
  background:rgba(0,0,0,.04);border-radius:var(--r);
  transform-origin:bottom center;
  transform:perspective(500px) rotateX(45deg);
  opacity:0;
  transition:transform .42s cubic-bezier(.22,1,.36,1),opacity .3s ease;
  z-index:0;pointer-events:none}
.snap-geo .cp-item-row:hover::before,
.snap-geo .cp-item.open .cp-item-row::before{
  transform:perspective(500px) rotateX(0deg);opacity:1}
.snap-geo .cp-item-row>*{position:relative;z-index:1}
.snap-geo .cp-item-body{border-top-color:#1a1a1a}

/* ── snap-cal: big full-height slides ───────────────────── */
.snap-cal>.section{height:100%;display:flex;flex-direction:column;overflow:hidden}
.snap-cal .cal-band{
  flex:1;min-height:0;
  display:flex;flex-direction:row;align-items:stretch;
  overflow-x:auto;overflow-y:hidden;
  gap:10px;
  /* 34% side padding = 50% - half expanded width (68%/2) — lets edge cards reach centre */
  padding:14px 34% 18px;
  -webkit-overflow-scrolling:touch;
  scrollbar-width:none}
.snap-cal .cal-band::-webkit-scrollbar{display:none}
/* big slide card — narrow by default, widens on hover/focus */
.snap-cal .cal-ev-card{
  flex:0 0 18%;
  position:relative;border-radius:12px;overflow:hidden;
  cursor:pointer;
  opacity:.45;
  will-change:flex-basis,opacity;
  transition:flex-basis .38s cubic-bezier(.25,0,.1,1),
             opacity .3s ease,
             box-shadow .25s ease}
.snap-cal .cal-ev-card.ev-center{
  flex:0 0 68%;
  opacity:1;
  box-shadow:0 12px 48px rgba(0,0,0,.55)}
.snap-cal .cal-ev-card.ev-past{opacity:.22;cursor:default}
.snap-cal .cal-ev-card.ev-past.ev-center{opacity:.4}
/* card background — photo or colour gradient */
.snap-cal .cal-ev-bg{
  position:absolute;inset:0;
  background:linear-gradient(135deg,var(--evc,#555) 0%,rgba(8,8,8,.97) 65%)}
/* darken photo with a simple gradient — no mix-blend-mode so photos show */
.snap-cal .cal-ev-bg::after{
  content:'';position:absolute;inset:0;
  background:linear-gradient(to bottom,rgba(0,0,0,.25) 0%,rgba(0,0,0,.65) 100%)}
.snap-cal .cal-ev-card.ev-past .cal-ev-bg{filter:grayscale(.7)}
/* bottom text layer */
.snap-cal .cal-ev-body{
  position:absolute;bottom:0;left:0;right:0;
  padding:28px 32px 32px;
  background:linear-gradient(to top,rgba(0,0,0,.75) 0%,transparent 100%);
  transition:transform .35s ease}
.snap-cal .cal-ev-card.ev-open .cal-ev-body{transform:translateY(-8px)}
.snap-cal .cal-ev-meta{display:flex;align-items:center;gap:8px;margin-bottom:12px}
.snap-cal .cal-ev-cat-chip{
  font-size:9px;font-weight:700;letter-spacing:1.1px;text-transform:uppercase;
  color:var(--evc,#fff);background:rgba(255,255,255,.1);
  border:1px solid rgba(255,255,255,.18);border-radius:4px;padding:3px 9px}
.snap-cal .cal-live-badge{
  font-size:9px;font-weight:700;letter-spacing:.6px;
  color:#fff;background:#16A34A;
  border-radius:4px;padding:3px 8px;
  animation:live-pulse 2s ease-in-out infinite}
@keyframes live-pulse{0%,100%{opacity:1}50%{opacity:.65}}
.snap-cal .cal-ev-name{
  font-size:clamp(22px,2.8vw,38px);font-weight:700;
  color:#fff;line-height:1.15;margin-bottom:8px;
  text-shadow:0 2px 12px rgba(0,0,0,.5)}
.snap-cal .cal-ev-range{font-size:13px;color:rgba(255,255,255,.55);font-weight:300}
/* articles panel slides up */
.snap-cal .cal-ev-panel{
  position:absolute;left:0;right:0;bottom:0;
  height:58%;
  background:rgba(4,4,4,.88);
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  padding:20px 32px 24px;
  overflow-y:auto;scrollbar-width:none;
  transform:translateY(100%);
  transition:transform .38s ease}
.snap-cal .cal-ev-panel::-webkit-scrollbar{display:none}
.snap-cal .cal-ev-card.ev-open .cal-ev-panel{transform:translateY(0)}
.snap-cal .cal-ev-panel-hd{
  font-size:9px;font-weight:700;letter-spacing:1px;
  text-transform:uppercase;color:rgba(255,255,255,.4);margin-bottom:12px}
.snap-cal .cal-det-art{
  display:flex;flex-direction:column;gap:3px;
  padding:10px 0;border-bottom:1px solid rgba(255,255,255,.08);
  text-decoration:none}
.snap-cal .cal-det-art:last-of-type{border-bottom:none}
.snap-cal .cal-det-art-title{font-size:12px;color:#fff;line-height:1.4;font-weight:400}
.snap-cal .cal-det-art-meta{font-size:9.5px;color:rgba(255,255,255,.4)}
.snap-cal .cal-det-none{font-size:11px;color:rgba(255,255,255,.4);margin:4px 0}
.snap-cal .cal-search-link{
  display:inline-block;margin-top:12px;
  font-size:10px;font-weight:600;color:rgba(255,255,255,.5);
  text-decoration:none;border-bottom:1px solid rgba(255,255,255,.2)}
.snap-cal .cal-search-link:hover{color:#fff}

/* ── Mobile snap overrides ───────────────────────────────── */
@media(max-width:768px){
  html{scroll-snap-type:none}
  .snap-sec{height:auto;overflow:visible}

  /* hero */
  .hero-sec{min-height:100svh;padding:56px 20px 72px}
  .hero-h1{font-size:clamp(42px,13vw,68px);letter-spacing:-2px;margin-bottom:20px}
  .hero-meta{margin-bottom:20px}
  .hero-hint{display:none}
  .hero-sec .ticker{position:relative;bottom:auto;margin-top:24px}

  /* geo map — kill the !important locks so height:auto works */
  .snap-geo{height:auto!important;overflow:visible!important}
  .snap-geo>.section{height:auto!important;display:block!important}
  .snap-geo .map-wrap{flex-direction:column;height:auto!important;min-height:0!important}
  .snap-geo #map{height:52vw!important;min-height:220px;max-height:320px;width:100%!important}
  .snap-geo .cp{height:280px;width:100%}

  /* feed: reset flex column so sections just stack */
  .snap-feed{display:block}
  .snap-feed>.two-col{flex:none;height:auto}
  .snap-feed .two-col>.section{height:auto}
  .snap-feed .story-list{max-height:380px}
  /* polymarket band: fixed height, no flex weirdness */
  .poly-band{flex:none;height:180px;min-height:0}
  .poly-card{flex:0 0 200px}
  .poly-card-q{font-size:11px}
  .poly-out-name{font-size:10px}
  .poly-out-pct{font-size:9.5px}
  .poly-card-vol{font-size:8px}

  /* culture */
  .snap-culture>.section{height:auto}
  .snap-culture .culture-body{flex:none;display:block}
  .snap-culture .cards{
    flex:none;display:flex;flex-direction:row;
    grid-template-rows:unset;grid-auto-flow:unset;
    grid-auto-columns:unset;
    height:52vw;min-height:180px;
    overflow-x:auto;overflow-y:hidden;
    padding:10px 16px 8px;gap:8px}
  .snap-culture .card{flex:0 0 65vw;height:100%}
  .snap-culture .culture-cal-band{
    padding:8px 10% 10px;gap:6px;min-height:160px}
  .snap-culture .culture-cal-band .cal-ev-card{flex:0 0 55%}
  .snap-culture .culture-cal-band .cal-ev-card.ev-center{flex:0 0 80%}

  /* bottom */
  .snap-bottom>.three-col{height:auto}
  .snap-bottom .three-col>.section{height:auto!important}
  .snap-bottom .story-list,.snap-bottom .paris-list{max-height:300px}

  /* cal */
  .snap-cal>.section{height:auto;overflow:visible}
  .snap-cal .cal-band{flex-wrap:nowrap;padding:12px 16px;overflow-x:auto}
  .snap-cal .cal-ev-name{font-size:clamp(18px,5vw,28px)}
}
"""
# ══════════════════════════════════════════════════════════════════════════════
#  HTML BUILDERS
# ══════════════════════════════════════════════════════════════════════════════
def _sec(color, label, body, extra_style=""):
    style = f"style='{extra_style}'" if extra_style else ""
    return (
        f'<div class="section" {style}>'
        f'<div class="sec-hd" style="border-top:2px solid {color}">'
        f'<span class="sec-hd-text">{label}</span>'
        f'</div>'
        f'{body}</div>\n'
    )
def build_ticker(items):
    if not items:
        return ""
    once = "".join(
        f'<a href="{_s(a["link"])}" target="_blank" rel="noopener" class="t-item">'
        f'{_s(a["title"])}</a>'
        for a in items[:12]
    )
    # Duplicate for seamless infinite scroll (translateX(-50%) loops back to start)
    links = once + once
    return (
        f'<div class="ticker">'
        f'<span class="ticker-label">AFP</span>'
        f'<div class="ticker-track"><div class="ticker-items">{links}</div></div>'
        f'</div>\n'
    )
def build_map(conflicts_json, articles_json):
    return f"""
<div class="section">
  <div class="sec-hd" style="border-top:2px solid #D42B17">
    <span class="sec-hd-text">Geopolitical Flashpoints</span>
    <span class="sec-hd-meta">● conflict / tension</span>
  </div>
  <div class="map-wrap">
    <div id="map"></div>
    <div class="cp">
      <div id="cp-list" class="cp-list"></div>
    </div>
  </div>
</div>
<script>
(function(){{
  var C = {conflicts_json};
  var A = {articles_json};
  var TC = {{ conflict:'#EF4444', tension:'#EF4444' }};
  var listEl = document.getElementById('cp-list');
  var markers = {{}};
  var GRID_SPACING = 8; /* must match canvas SPACING below */

  function _snapToGrid(latlng) {{
    /* snap a lat/lng to the nearest canvas grid dot */
    var px   = map.latLngToContainerPoint(latlng);
    var half = GRID_SPACING / 2;
    var sx   = Math.round((px.x - half) / GRID_SPACING) * GRID_SPACING + half;
    var sy   = Math.round((px.y - half) / GRID_SPACING) * GRID_SPACING + half;
    return map.containerPointToLatLng([sx, sy]);
  }}

  /* ── Leaflet map: static (no pan/zoom), canvas handles base map ── */
  var map = L.map('map',{{center:[20,10],zoom:2,minZoom:2,maxZoom:2,
    zoomControl:false,attributionControl:false,
    dragging:false,scrollWheelZoom:false,doubleClickZoom:false,
    touchZoom:false,keyboard:false,boxZoom:false,
    maxBounds:[[-80,-200],[85,200]],maxBoundsViscosity:1.0}});

  /* ── Canvas dot-world ───────────────────────────────────────── */
  var mapEl = document.getElementById('map');
  var dotCanvas = document.createElement('canvas');
  dotCanvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:250';
  mapEl.appendChild(dotCanvas);
  var _landGeo = null;

  function _drawDotWorld() {{
    if (!_landGeo) return;
    var w = mapEl.offsetWidth, h = mapEl.offsetHeight;
    if (!w || !h) return;
    dotCanvas.width  = w;
    dotCanvas.height = h;

    /* Step 1 — rasterise land polygons onto a temporary canvas */
    var tmp = document.createElement('canvas');
    tmp.width = w; tmp.height = h;
    var tc = tmp.getContext('2d');
    tc.fillStyle = '#fff';

    function drawRings(rings) {{
      rings.forEach(function(ring) {{
        tc.beginPath();
        var prevLng = null;
        for (var i = 0; i < ring.length; i++) {{
          var lng = ring[i][0], lat = ring[i][1];
          /* skip antimeridian wrap artefacts */
          if (prevLng !== null && Math.abs(lng - prevLng) > 180) {{
            tc.closePath(); tc.fill(); tc.beginPath();
          }}
          var px = map.latLngToContainerPoint(L.latLng(lat, lng));
          if (i === 0) tc.moveTo(px.x, px.y);
          else         tc.lineTo(px.x, px.y);
          prevLng = lng;
        }}
        tc.closePath();
        tc.fill();
      }});
    }}

    _landGeo.features.forEach(function(f) {{
      var g = f.geometry;
      if (g.type === 'Polygon')      drawRings(g.coordinates);
      else if (g.type === 'MultiPolygon')
        g.coordinates.forEach(function(poly) {{ drawRings(poly); }});
    }});

    /* Step 2 — pixel-test and place dots on the real canvas */
    var imgData = tc.getImageData(0, 0, w, h).data;
    var ctx = dotCanvas.getContext('2d');
    var SPACING = GRID_SPACING, R = 2.4;
    ctx.fillStyle = 'rgba(190,190,190,0.9)';

    for (var y = SPACING / 2; y < h; y += SPACING) {{
      for (var x = SPACING / 2; x < w; x += SPACING) {{
        var xi = Math.min(Math.floor(x), w - 1);
        var yi = Math.min(Math.floor(y), h - 1);
        var idx = (yi * w + xi) * 4;
        if (imgData[idx] > 128) {{
          ctx.beginPath();
          ctx.arc(x, y, R, 0, Math.PI * 2);
          ctx.fill();
        }}
      }}
    }}
  }}

  /* Load world land topojson (120 KB) */
  fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/land-110m.json')
    .then(function(r) {{ return r.json(); }})
    .then(function(topo) {{
      _landGeo = topojson.feature(topo, topo.objects.land);
      _drawDotWorld();
    }})
    .catch(function(e) {{ console.warn('world-atlas load failed', e); }});

  window.addEventListener('resize', function() {{ map.invalidateSize(); _drawDotWorld(); }});
  function _esc(t) {{
    return String(t||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }}
  function isNew(id) {{
    var arts = A[id] || [];
    if (!arts.length) return false;
    var seen = parseInt(localStorage.getItem('seen_'+id)||'0');
    return arts.some(function(a){{ return (a.ts||0)*1000 > seen; }});
  }}
  function markSeen(id) {{
    localStorage.setItem('seen_'+id, Date.now());
  }}
  function _replacePulse(c) {{
    if (markers[c.id] && markers[c.id]._isPulse) {{
      markers[c.id].remove();
      var cls = 'gm-dot '+(c.type==='conflict'?'gm-conflict':'gm-tension');
      var snapped = _snapToGrid(L.latLng(c.lat,c.lon));
      var m = L.marker(snapped,{{icon:L.divIcon({{
        className:'',html:'<div class="'+cls+'"></div>',
        iconSize:[5,5],iconAnchor:[2,2]
      }})}}).addTo(map);
      m.bindTooltip(c.name,{{direction:'top',opacity:.9}});
      m.on('click',function(){{toggleItem(c.id);}});
      markers[c.id]=m;
    }}
  }}
  function toggleItem(id) {{
    var c = C.find(function(x){{return x.id===id;}});
    if (!c) return;
    var item = listEl.querySelector('[data-id="'+id+'"]');
    if (!item) return;
    var wasOpen = item.classList.contains('open');
    /* close all */
    listEl.querySelectorAll('.cp-item.open').forEach(function(el){{
      el.classList.remove('open');
    }});
    if (!wasOpen) {{
      item.classList.add('open');
      markSeen(id);
      _replacePulse(c);
      item.classList.remove('has-new');
      item.scrollIntoView({{behavior:'smooth',block:'nearest'}});
    }}
  }}

  /* Build accordion list */
  C.forEach(function(c){{
    var col    = TC[c.type]||'#888';
    var hasNew = isNew(c.id);
    var arts   = A[c.id]||[];
    var artsHtml = arts.length
      ? arts.map(function(a){{
          return '<a href="'+_esc(a.link)+'" target="_blank" rel="noopener" class="cp-art">'
            +_esc(a.title)+'<br><small>'+_esc(a.source)+(a.ago?' · '+a.ago:'')+'</small></a>';
        }}).join('')
      : '<p class="cp-no">No recent articles matched.</p>';

    var item = document.createElement('div');
    item.className='cp-item'+(hasNew?' has-new':''); item.dataset.id=c.id;
    item.innerHTML=
      '<div class="cp-item-row">'
        +'<span class="dot" style="background:'+col+'"></span>'
        +'<span class="cp-item-name">'+_esc(c.name)+'</span>'
        +'<span class="cp-chevron">›</span>'
      +'</div>'
      +'<div class="cp-item-body">'
        +'<div class="cp-meta">Since '+_esc(c.started)+'</div>'
        +'<div class="cp-sum">'+_esc(c.summary)+'</div>'
        +(arts.length?'<div class="cp-arts-hd">Recent Coverage</div>'+artsHtml:'')
      +'</div>';

    item.querySelector('.cp-item-row').addEventListener('click',function(){{toggleItem(c.id);}});
    listEl.appendChild(item);

    /* Map marker — snapped to nearest canvas grid dot */
    var snapped = _snapToGrid(L.latLng(c.lat,c.lon));
    var m;
    if (hasNew) {{
      var sonarCls = 'gm-dot '+(c.type==='conflict'?'gm-conflict':'gm-tension')+' gm-sonar';
      m = L.marker(snapped,{{icon:L.divIcon({{
        className:'',html:'<div class="'+sonarCls+'"></div>',
        iconSize:[5,5],iconAnchor:[2,2]
      }})}}).addTo(map);
      m._isPulse = true;
    }} else {{
      var dotCls = 'gm-dot '+(c.type==='conflict'?'gm-conflict':'gm-tension');
      m = L.marker(snapped,{{icon:L.divIcon({{
        className:'',html:'<div class="'+dotCls+'"></div>',
        iconSize:[5,5],iconAnchor:[2,2]
      }})}}).addTo(map);
    }}
    m.bindTooltip(c.name,{{direction:'top',opacity:.9}});
    m.on('click',function(){{toggleItem(c.id);}});
    markers[c.id]=m;
  }});
  setTimeout(function(){{map.invalidateSize();_drawDotWorld();}},300);
}})();
</script>
"""
def _load_openai():
    """Return an OpenAI client if configured, else None."""
    import os
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key and OPENAI_CONFIG_FILE.exists():
        try:
            api_key = json.loads(OPENAI_CONFIG_FILE.read_text()).get("api_key")
        except Exception:
            pass
    if not api_key:
        print("  ⚠  OpenAI: no API key found — skipping AI headlines")
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key)
    except ImportError:
        print("  ⚠  OpenAI: package not installed — run: pip install openai")
        return None

def _load_headline_cache():
    if HEADLINE_CACHE_FILE.exists():
        try:
            return json.loads(HEADLINE_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_headline_cache(cache):
    try:
        HEADLINE_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    except Exception:
        pass

def _ai_headline(titles, client, cache):
    """Generate a clean one-line headline for a story group via GPT-4o-mini."""
    cache_key = "||".join(sorted(t.strip().lower() for t in titles[:8]))
    if cache_key in cache:
        return cache[cache_key]
    try:
        prompt = (
            "These headlines from different sources all cover the same news story.\n"
            "Write ONE clean, factual headline in English (max 12 words) capturing the core event.\n"
            "Rules: no source names, no opinion words, just the fact, no quotes around output.\n\n"
            + "\n".join(f"- {t}" for t in titles[:8])
            + "\n\nHeadline:"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=40,
            temperature=0.2,
        )
        headline = resp.choices[0].message.content.strip().strip('"').strip("'")
        cache[cache_key] = headline
        return headline
    except Exception as e:
        print(f"  ⚠  AI headline: {e}")
        return titles[0]

def _enrich_groups(groups, client, cache):
    """Add AI-generated headline to every multi-source story group."""
    if not client:
        return groups
    result = []
    for g in groups:
        if len(g) > 1:
            titles   = [a["title"] for a in g]
            headline = _ai_headline(titles, client, cache)
            g        = [dict(g[0], _headline=headline)] + list(g[1:])
        result.append(g)
    return result

def _build_group_row(g, extra_cls="", data_attrs=""):
    """Render a story group. Single article → direct link. Multiple → click to expand."""
    primary  = g[0]
    n        = len(g)
    time_str = _ago(primary["date"])
    attrs    = f" {data_attrs}" if data_attrs else ""
    if n == 1:
        return (
            f'<div class="sg{" "+extra_cls if extra_cls else ""}"{attrs}>'
            f'<a href="{_s(primary["link"])}" target="_blank" rel="noopener" class="sg-title">'
            f'{_s(primary["title"])}</a>'
            f'<span class="badge">{_s(primary["source"])}</span>'
            f'<span class="sg-time">{time_str}</span>'
            f'</div>\n'
        )
    arts_html = "".join(
        f'<a href="{_s(a["link"])}" target="_blank" rel="noopener" '
        f'class="sg-art-link" onclick="event.stopPropagation()">'
        f'<span class="sg-art-src">{_s(a.get("_canon") or a["source"])}</span>'
        f'<span class="sg-art-ttl">{_s(a["title"])}</span>'
        f'<span class="sg-art-time">{_ago(a["date"])}</span>'
        f'</a>'
        for a in g
    )
    display_title = primary.get("_headline") or primary["title"]
    return (
        f'<div class="sg sg-multi{" "+extra_cls if extra_cls else ""}"{attrs} '
        f'onclick="this.classList.toggle(\'open\')">'
        f'<div class="sg-hd">'
        f'<span class="sg-title">{_s(display_title)}</span>'
        f'<span class="sg-cnt">{n} sources</span>'
        f'<span class="sg-time">{time_str}</span>'
        f'</div>'
        f'<div class="sg-arts">{arts_html}</div>'
        f'</div>\n'
    )

def _fetch_art_newspaper(n=5):
    """Always return the latest n NYT Arts articles, ignoring age filter."""
    try:
        arts = _fetch([("The NYT Arts", ART_NEWSPAPER_FEED)])
        arts.sort(key=lambda a: a["ts"] or 0, reverse=True)
        return arts[:n]
    except Exception as ex:
        print(f"  ⚠  NYT Arts: {ex}")
        return []

def _fetch_polymarket(limit=16):
    """Fetch top Polymarket markets by 24h volume."""
    try:
        import urllib.request, json as _json
        url = ("https://gamma-api.polymarket.com/markets"
               "?active=true&closed=false&limit=25&order=volume24hr&ascending=false")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read())
        markets = []
        for m in data:
            q = (m.get("question") or "").strip()
            if not q or len(q) < 8:
                continue
            # outcomes + prices
            try:
                outcomes_raw = m.get("outcomes")
                outcomes = _json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])
                prices_raw = m.get("outcomePrices")
                prices = _json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])
                pairs = [(outcomes[i], round(float(prices[i]) * 100))
                         for i in range(min(len(outcomes), len(prices)))]
                pairs.sort(key=lambda x: -x[1])
                pairs = pairs[:3]
            except Exception:
                pairs = []
            # volume
            try:
                vol = float(m.get("volume") or m.get("volume24hr") or 0)
                if vol >= 1e9:   vol_str = f"${vol/1e9:.1f}B Vol."
                elif vol >= 1e6: vol_str = f"${vol/1e6:.0f}M Vol."
                elif vol >= 1e3: vol_str = f"${vol/1e3:.0f}K Vol."
                else:            vol_str = f"${vol:.0f} Vol."
            except Exception:
                vol_str = ""
            slug = m.get("slug") or ""
            markets.append({"q": q, "pairs": pairs, "vol": vol_str, "slug": slug})
            if len(markets) >= limit:
                break
        print(f"    → {len(markets)} Polymarket markets")
        return markets
    except Exception as ex:
        print(f"  ⚠  Polymarket: {ex}")
        return []

def build_polymarket_band(markets):
    if not markets:
        return ""
    def fmt_pct(p):
        if p < 1:  return "<1%"
        if p > 99: return ">99%"
        return f"{p}%"
    def card_html(m):
        link = (f'https://polymarket.com/event/{_s(m["slug"])}'
                if m.get("slug") else "https://polymarket.com")
        outcomes_html = "".join(
            f'<div class="poly-outcome">'
            f'<span class="poly-out-name">{_s(name)}</span>'
            f'<span class="poly-out-pct {"high" if pct>=50 else "low"}">{fmt_pct(pct)}</span>'
            f'</div>'
            for name, pct in m["pairs"]
        )
        vol = f'<div class="poly-card-vol">{_s(m["vol"])}</div>' if m.get("vol") else ""
        return (
            f'<a href="{link}" target="_blank" rel="noopener" class="poly-card">'
            f'<div class="poly-card-q">{_s(m["q"])}</div>'
            f'<div class="poly-card-outcomes">{outcomes_html}</div>'
            f'{vol}</a>'
        )
    once = "".join(card_html(m) for m in markets)
    items = once + once   # duplicate for seamless loop
    return (
        f'<div class="poly-band">'
        f'<div class="sec-hd"><span class="sec-hd-text">Polymarket</span></div>'
        f'<div class="poly-band-track">'
        f'<div class="poly-band-items">{items}</div>'
        f'</div></div>\n'
    )

def build_price_band():
    # (sym, display_name, coingecko_id or None)
    tickers = [
        ("BTC-USD",  "Bitcoin",   "bitcoin"),
        ("ETH-USD",  "Ethereum",  "ethereum"),
        ("^GSPC",    "S&P 500",   None),
        ("^IXIC",    "NASDAQ",    None),
        ("^DJI",     "Dow Jones", None),
        ("GC=F",     "Gold",      None),
        ("CL=F",     "WTI Oil",   None),
        ("EURUSD=X", "EUR/USD",   None),
        ("^VIX",     "VIX",       None),
        ("^TNX",     "10Y UST",   None),
    ]
    tickers_js = "[" + ",".join(
        f'{{sym:{repr(s)},name:{repr(n)},cg:{repr(cg) if cg else "null"}}}'
        for s, n, cg in tickers
    ) + "]"
    return f"""<div class="price-band">
  <div class="sec-hd"><span class="sec-hd-text">Markets</span></div>
  <div class="price-band-track" id="price-band-track">
    <span class="price-tile-loading">Loading prices…</span>
  </div>
</div>
<script>
(function(){{
  var TICKERS = {tickers_js};
  function fmt(v, sym) {{
    if (v == null) return '—';
    if (sym === 'EURUSD=X') return v.toFixed(4);
    if (sym === '^TNX') return v.toFixed(2) + '%';
    if (v >= 10000) return v.toLocaleString('en-US', {{maximumFractionDigits:0}});
    if (v >= 1000)  return v.toLocaleString('en-US', {{maximumFractionDigits:1}});
    if (v >= 100)   return v.toFixed(2);
    return v.toFixed(2);
  }}
  function load() {{
    var yahooSyms = TICKERS.filter(function(t){{return t.cg==null;}}).map(function(t){{return t.sym;}});
    var cgIds     = TICKERS.filter(function(t){{return t.cg!=null;}}).map(function(t){{return t.cg;}});
    var quotes    = {{}};
    var pending   = 2;
    function done(){{ if(--pending===0) render(quotes); }}

    /* CoinGecko — crypto, always 24/7 */
    fetch('https://api.coingecko.com/api/v3/simple/price?ids='+cgIds.join(',')+'&vs_currencies=usd&include_24hr_change=true')
      .then(function(r){{return r.json();}})
      .then(function(data){{
        TICKERS.forEach(function(t){{
          if (t.cg && data[t.cg]) {{
            quotes[t.sym] = {{
              regularMarketPrice: data[t.cg].usd,
              regularMarketChangePercent: data[t.cg].usd_24h_change
            }};
          }}
        }});
      }})
      .catch(function(){{}})
      .finally(done);

    /* Yahoo Finance — indices, forex, commodities */
    fetch('https://query1.finance.yahoo.com/v7/finance/quote?symbols='+encodeURIComponent(yahooSyms.join(','))+'&fields=regularMarketPrice,regularMarketChangePercent')
      .then(function(r){{return r.json();}})
      .then(function(data){{
        ((data.quoteResponse||{{}}).result||[]).forEach(function(q){{
          quotes[q.symbol] = q;
        }});
      }})
      .catch(function(){{}})
      .finally(done);
  }}
  function render(quotes) {{
    var track = document.getElementById('price-band-track');
    if (!track) return;
    track.innerHTML = '';
    TICKERS.forEach(function(t) {{
      var q = quotes[t.sym] || {{}};
      var price = q.regularMarketPrice;
      var pct   = q.regularMarketChangePercent;
      var tile  = document.createElement('div');
      tile.className = 'price-tile';
      var chgHtml = '';
      if (pct != null) {{
        var cls  = pct >= 0 ? 'up' : 'dn';
        var sign = pct >= 0 ? '+' : '';
        chgHtml = '<div class="price-tile-chg '+cls+'">'+sign+pct.toFixed(2)+'%</div>';
      }}
      tile.innerHTML =
        '<div class="price-tile-name">'+t.name+'</div>'+
        '<div class="price-tile-val">'+(price!=null ? fmt(price,t.sym) : '—')+'</div>'+
        chgHtml;
      track.appendChild(tile);
    }});
  }}
  load();
  setInterval(load, 60000);
}})();
</script>
"""

def _sort_by_time(groups):
    """Sort groups purely by recency of their most recent article."""
    return sorted(groups, key=lambda g: max(a["ts"] or 0 for a in g), reverse=True)

def build_tech(groups):
    rows = "".join(_build_group_row(g) for g in _sort_by_time(groups)[:50])
    if not rows:
        rows = '<p style="font-size:11px;color:var(--dim)">No articles in the past 48h.</p>'
    return _sec("#0C0C0C","Tech — Startups — VC",
                f'<div class="story-list">{rows}</div>')

def build_macro(groups):
    rows = "".join(_build_group_row(g) for g in _sort_by_time(groups)[:40])
    if not rows:
        rows = '<p style="font-size:11px;color:var(--dim)">No articles in the past 48h.</p>'
    return _sec("#0C0C0C","Macro — Finance — Markets",
                f'<div class="story-list">{rows}</div>')

def _build_cal_band_html(event_news={}):
    """Returns the HTML+JS for the compact event band embedded in the culture section."""
    today     = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    cat_col = {
        "culture":"#7C3AED","fashion":"#EA580C","football":"#15803D",
        "f1":"#DC2626","horses":"#B45309","swimming":"#1D4ED8",
        "rowing":"#0E7490","sailing":"#0F766E","tennis":"#0284C7",
        "golf":"#166534","cycling":"#D97706","rugby":"#7E22CE",
        "music":"#DB2777","tech":"#0369A1","finance":"#374151",
    }
    cat_lbl = {
        "culture":"Culture","fashion":"Fashion","football":"Football",
        "f1":"F1","horses":"Horses","swimming":"Swimming",
        "rowing":"Rowing","sailing":"Sailing","tennis":"Tennis",
        "golf":"Golf","cycling":"Cycling","rugby":"Rugby",
        "music":"Music","tech":"Tech","finance":"Finance",
    }
    mon_abbr = ["","Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"]
    def fmt_range(s_str, e_str):
        from datetime import datetime as _dt
        s = _dt.strptime(s_str, "%Y-%m-%d")
        e = _dt.strptime(e_str, "%Y-%m-%d")
        if s_str == e_str: return f"{s.day} {mon_abbr[s.month]}"
        if s.year == e.year and s.month == e.month:
            return f"{s.day}–{e.day} {mon_abbr[s.month]}"
        if s.year != e.year:
            return f"{s.day} {mon_abbr[s.month]} – {e.day} {mon_abbr[e.month]} {e.year}"
        span = (e - s).days
        if span > 60: return f"{mon_abbr[s.month]} – {mon_abbr[e.month]} {s.year}"
        return f"{s.day} {mon_abbr[s.month]} – {e.day} {mon_abbr[e.month]}"
    def _ev_sort(e):
        if e["start"] <= today_str <= e["end"]: return (0, e["start"])
        if e["end"] >= today_str:               return (1, e["start"])
        return                                        (2, e["start"])
    sorted_evs = sorted(CALENDAR_EVENTS, key=_ev_sort)
    scroll_idx = 0
    for i, e in enumerate(sorted_evs):
        if e["end"] >= today_str:
            scroll_idx = i
            break
    cards_html = ""
    for i, ev in enumerate(sorted_evs):
        col     = cat_col.get(ev["cat"], "#555")
        lbl     = cat_lbl.get(ev["cat"], ev["cat"])
        rng     = fmt_range(ev["start"], ev["end"])
        is_live = ev["start"] <= today_str <= ev["end"]
        is_past = ev["end"] < today_str
        cls     = (" ev-live" if is_live else " ev-past" if is_past else "")
        live_badge = '<span class="cal-live-badge">LIVE</span>' if is_live else ""
        arts = event_news.get(ev["name"], [])
        card_img = next((a["img"] for a in arts if a.get("img")), "")
        arts_html = ""
        for a in arts:
            arts_html += (
                f'<a href="{_s(a.get("link","#"))}" target="_blank" rel="noopener" class="cal-det-art">'
                f'<span class="cal-det-art-title">{_s(a.get("title",""))}</span>'
                f'<span class="cal-det-art-meta">{_s(a.get("source",""))}'
                f'{(" · "+_s(a["ago"])) if a.get("ago") else ""}</span>'
                f'</a>'
            )
        if not arts_html:
            arts_html = '<p class="cal-det-none">No recent coverage.</p>'
        search_url = f'https://www.google.com/search?q={_s(ev["name"]+" 2026")}'
        if card_img:
            bg_style = (f"background-image:url({_s(card_img)});"
                        f"background-size:cover;background-position:center top")
        else:
            bg_style = (f"background:linear-gradient(135deg,{col} 0%,"
                        f"rgba(8,8,8,.97) 65%)")
        cards_html += (
            f'<div class="cal-ev-card{cls}" id="ccv-{i}" style="--evc:{col}">'
            f'<div class="cal-ev-bg" style="{bg_style}"></div>'
            f'<div class="cal-ev-body">'
            f'<div class="cal-ev-meta">'
            f'<span class="cal-ev-cat-chip">{_s(lbl)}</span>'
            f'{live_badge}'
            f'</div>'
            f'<div class="cal-ev-name">{_s(ev["name"])}</div>'
            f'<div class="cal-ev-range">{_s(rng)}</div>'
            f'</div>'
            f'<div class="cal-ev-panel">'
            f'<div class="cal-ev-panel-hd">Recent Coverage</div>'
            f'{arts_html}'
            f'<a href="{search_url}" target="_blank" rel="noopener" class="cal-search-link">Search Google →</a>'
            f'</div>'
            f'</div>\n'
        )
    js = f"""<script>
(function(){{
  var band=document.getElementById('culture-cal-band');
  var all=[].slice.call(band.querySelectorAll('.cal-ev-card'));
  function setCenter(card){{
    all.forEach(function(c){{c.classList.remove('ev-center');}});
    if(card)card.classList.add('ev-center');
  }}
  function scrollToCard(card){{
    var target=card.offsetLeft+card.offsetWidth/2-band.clientWidth/2;
    band.scrollTo({{left:Math.max(0,target),behavior:'smooth'}});
  }}
  all.forEach(function(card){{
    if(card.classList.contains('ev-past'))return;
    card.addEventListener('click',function(){{
      var isOpen=card.classList.contains('ev-open');
      all.forEach(function(c){{c.classList.remove('ev-open');c.classList.remove('ev-center');}});
      if(!isOpen){{
        card.classList.add('ev-open');
        card.classList.add('ev-center');
        setTimeout(function(){{scrollToCard(card);}},80);
      }}
    }});
  }});
  var si=document.getElementById('ccv-{scroll_idx}');
  if(si&&!si.classList.contains('ev-past')){{
    setCenter(si);
    setTimeout(function(){{
      var target=si.offsetLeft+si.offsetWidth/2-band.clientWidth/2;
      band.scrollLeft=Math.max(0,target);
    }},120);
  }}
}})();
</script>"""
    return f'<div class="culture-cal-band" id="culture-cal-band">{cards_html}</div>{js}'

def build_culture(arts, event_news={}):
    html_cards = ""
    for a in arts[:48]:
        img  = a.get("img","")
        snip = _s(a.get("snip","") or "")
        bg   = (f"background-image:url({_s(img)});background-size:cover;background-position:center;"
                if img else "background:linear-gradient(135deg,#4a1040,#1a0a2e);")
        snip_html = f'<div class="cv-snip">{snip}</div>' if snip else ""
        html_cards += (
            f'<a href="{_s(a["link"])}" target="_blank" rel="noopener" class="card">'
            f'<div class="ci" style="{bg}"></div>'
            f'<span class="cs">{_s(a["source"])}</span>'
            f'<div class="cb"><p class="ct">{_s(a["title"])}</p>'
            f'<span class="ctime">{_ago(a["date"])}</span></div>'
            f'<div class="cv-overlay">'
            f'<div class="cv-src">{_s(a["source"])}</div>'
            f'<div class="cv-title">{_s(a["title"])}</div>'
            f'{snip_html}'
            f'<div class="cv-footer">'
            f'<span class="cv-time">{_ago(a["date"])}</span>'
            f'<span class="cv-read">Read article →</span>'
            f'</div></div>'
            f'</a>\n'
        )
    js_culture = """<script>
(function(){
  var cc=[].slice.call(document.querySelectorAll('.snap-culture .card'));
  cc.forEach(function(card){
    card.addEventListener('click',function(e){
      if(card.classList.contains('cv-open')){
        card.classList.remove('cv-open');
        return;
      }
      e.preventDefault();
      cc.forEach(function(c){c.classList.remove('cv-open');});
      card.classList.add('cv-open');
    });
  });
  document.addEventListener('click',function(e){
    if(!e.target.closest('.snap-culture .card'))
      cc.forEach(function(c){c.classList.remove('cv-open');});
  });
})();
</script>"""
    cal_band = _build_cal_band_html(event_news)
    body = (f'<div class="culture-body">'
            f'<div class="cards">{html_cards}</div>'
            f'{cal_band}'
            f'</div>{js_culture}')
    return _sec("#D42B17","Fashion &amp; Culture", body)

def build_sports(groups):
    rows = "".join(_build_group_row(g) for g in _sort_groups(groups)[:30])
    if not rows:
        rows = '<p style="font-size:11px;color:var(--dim)">No articles fetched.</p>'
    return _sec("#0C0C0C","Sports", f'<div class="story-list">{rows}</div>')

def build_cities(groups):
    rows = ""
    for g in _sort_groups(groups)[:40]:
        city = "marseille" if g[0]["source"] in MARSEILLE_SOURCE_NAMES else "paris"
        rows += _build_group_row(g, extra_cls="city-item", data_attrs=f'data-city="{city}"')
    if not rows:
        rows = '<p style="font-size:11px;color:var(--dim)">No articles fetched.</p>'
    body = f"""<div class="story-list" id="city-list">{rows}</div>
<script>
function filterCity(v){{
  document.querySelectorAll('.fb[id^="city-"]').forEach(function(b){{
    b.classList.toggle('on', b.id==='city-'+v||(v==='all'&&b.id==='city-all'));
  }});
  document.querySelectorAll('.city-item').forEach(function(el){{
    el.style.display=(v==='all'||el.dataset.city===v)?'':'none';
  }});
}}
</script>"""
    hd_buttons = (
        '<div style="display:flex;gap:6px">'
        '<button class="fb on" id="city-all" onclick="filterCity(\'all\')">All</button>'
        '<button class="fb" id="city-marseille" onclick="filterCity(\'marseille\')">Marseille</button>'
        '<button class="fb" id="city-paris" onclick="filterCity(\'paris\')">Paris</button>'
        '</div>'
    )
    return (
        f'<div class="section">'
        f'<div class="sec-hd" style="border-top:2px solid #0C0C0C">'
        f'<span class="sec-hd-text">Marseille &amp; Paris</span>'
        f'{hd_buttons}'
        f'</div>'
        f'{body}</div>\n'
    )
def build_paris(arts):
    rows = ""
    for a in arts[:20]:
        rows += (
            f'<div class="pi">'
            f'<span class="pi-src">{_s(a["source"])}</span>'
            f'<a href="{_s(a["link"])}" target="_blank" rel="noopener" class="pi-title">'
            f'{_s(a["title"])}</a>'
            f'<span class="pi-t">{_ago(a["date"])}</span>'
            f'</div>\n'
        )
    if not rows:
        rows = '<p style="font-size:11px;color:var(--dim);padding:14px 0">No Paris events fetched — feeds may be unavailable.</p>'
    return _sec("#0C0C0C","Paris — What’s On",
                f'<div class="paris-list">{rows}</div>')
def build_calendar_OLD(event_news={}):
    today     = datetime.now()
    today_str = today.strftime("%Y-%m-%d")

    cat_col = {
        "culture":"#7C3AED","fashion":"#EA580C","football":"#15803D",
        "f1":"#DC2626","horses":"#B45309","swimming":"#1D4ED8",
        "rowing":"#0E7490","sailing":"#0F766E","tennis":"#0284C7",
        "golf":"#166534","cycling":"#D97706","rugby":"#7E22CE",
        "music":"#DB2777","tech":"#0369A1","finance":"#374151",
    }
    cat_lbl = {
        "culture":"Culture","fashion":"Fashion","football":"Football",
        "f1":"F1","horses":"Horses","swimming":"Swimming",
        "rowing":"Rowing","sailing":"Sailing","tennis":"Tennis",
        "golf":"Golf","cycling":"Cycling","rugby":"Rugby",
        "music":"Music","tech":"Tech","finance":"Finance",
    }
    month_names = ["","January","February","March","April","May","June",
                   "July","August","September","October","November","December"]
    mon_abbr    = ["","Jan","Feb","Mar","Apr","May","Jun",
                   "Jul","Aug","Sep","Oct","Nov","Dec"]

    def fmt_range(s_str, e_str):
        from datetime import datetime as _dt
        s = _dt.strptime(s_str, "%Y-%m-%d")
        e = _dt.strptime(e_str, "%Y-%m-%d")
        if s_str == e_str:
            return f"{s.day} {mon_abbr[s.month]}"
        if s.year == e.year and s.month == e.month:
            return f"{s.day}–{e.day} {mon_abbr[s.month]}"
        span = (e - s).days
        if s.year != e.year:
            return f"{s.day} {mon_abbr[s.month]} – {e.day} {mon_abbr[e.month]} {e.year}"
        if span > 60:
            return f"{mon_abbr[s.month]} – {mon_abbr[e.month]} {s.year}"
        return f"{s.day} {mon_abbr[s.month]} – {e.day} {mon_abbr[e.month]}"

    # Jan 2026 → Dec 2027 = 24 months
    start_y, start_m = 2026, 1
    total = 24
    cur_idx = (today.year - start_y) * 12 + (today.month - start_m)
    cur_idx = max(0, min(cur_idx, total - 3))

    # Build a dict: (year, month) → sorted list of events starting that month
    monthly = {}
    for e in CALENDAR_EVENTS:
        sy = int(e["start"][:4]); sm = int(e["start"][5:7])
        monthly.setdefault((sy, sm), []).append(e)
    for k in monthly:
        monthly[k].sort(key=lambda e: e["start"])

    all_months = []
    cy, cm = start_y, start_m
    for idx in range(total):
        events = monthly.get((cy, cm), [])
        if events:
            rows = ""
            for e in events:
                col = cat_col.get(e["cat"], "#555")
                rng = fmt_range(e["start"], e["end"])
                has_news = e["name"] in event_news
                ev_attr  = f' data-ev="{_s(e["name"])}" data-range="{_s(rng)}"' if has_news else ""
                # Don't dim past events if they have news (still clickable/relevant)
                if e["end"] < today_str and not has_news:
                    cls = " ev-past"
                elif e["start"] <= today_str <= e["end"]:
                    cls = " ev-live"
                else:
                    cls = ""
                rows += (
                    f'<div class="cal-erow{cls}"{ev_attr}>'
                    f'<span class="cal-edot" style="background:{col}"></span>'
                    f'<span class="cal-ename">{_s(e["name"])}</span>'
                    f'<span class="cal-erange">{rng}</span>'
                    f'</div>\n'
                )
        else:
            rows = '<div class="cal-empty-msg">No events this month</div>'

        vis = "" if cur_idx <= idx < cur_idx + 3 else "display:none"
        all_months.append(
            f'<div class="cal-month" data-ci="{idx}" style="{vis}">'
            f'<div class="cal-mhd">{month_names[cm]} {cy}</div>'
            f'<div class="cal-elist">{rows}</div>'
            f'</div>'
        )
        cm += 1
        if cm > 12:
            cm = 1; cy += 1

    months_html = "".join(all_months)
    legend = "".join(
        f'<span class="cal-leg-i">'
        f'<span class="cal-leg-dot" style="background:{col}"></span>'
        f'{cat_lbl[cat]}</span>'
        for cat, col in cat_col.items()
    )

    event_news_js = json.dumps(event_news, ensure_ascii=False)

    body = f"""
<div style="display:flex;align-items:center;justify-content:space-between;
  padding:16px 40px 20px;border-bottom:1px solid var(--border);flex-wrap:wrap;gap:12px">
  <div class="cal-legend">{legend}</div>
  <div style="display:flex;align-items:center;gap:10px;flex-shrink:0">
    <button class="cal-btn" id="cal-prev">&#8592;</button>
    <span class="cal-nav-label" id="cal-range"></span>
    <button class="cal-btn" id="cal-next">&#8594;</button>
  </div>
</div>
<div class="cal-months" id="cal-months-wrap">{months_html}</div>
<div id="cal-det" style="display:none">
  <div class="cal-det-hd">
    <div class="cal-det-title">
      <a class="cal-det-name" id="cal-det-name" href="#" target="_blank" rel="noopener"></a>
      <span class="cal-det-range" id="cal-det-range"></span>
    </div>
    <button class="cal-det-close" id="cal-det-close">&#x2715;</button>
  </div>
  <div id="cal-det-body"></div>
</div>
<script>
(function(){{
  var cur={cur_idx},total={total};
  var names={json.dumps(month_names)};
  var sy={start_y},sm={start_m};
  var EN={event_news_js};
  var activeEv=null;
  function _esc(t){{return String(t||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}
  function getShow(){{return window.innerWidth<=768?1:window.innerWidth<=1100?2:3;}}
  function mname(idx){{
    var y=sy+Math.floor((sm-1+idx)/12),m=(sm-1+idx)%12;
    return names[m+1]+' '+y;
  }}
  function show(s){{
    var SHOW=getShow();
    document.querySelectorAll('.cal-month').forEach(function(el){{
      var i=parseInt(el.dataset.ci);
      el.style.display=(i>=s&&i<s+SHOW)?'':'none';
    }});
    document.getElementById('cal-prev').disabled=s<=0;
    document.getElementById('cal-next').disabled=s+SHOW>=total;
    document.getElementById('cal-range').textContent=mname(s)+' – '+mname(s+SHOW-1);
  }}
  function openDetail(name, range){{
    activeEv=name;
    var nameEl=document.getElementById('cal-det-name');
    nameEl.textContent=name;
    nameEl.href='https://www.google.com/search?q='+encodeURIComponent(name+' 2026');
    document.getElementById('cal-det-range').textContent=range;
    var arts=EN[name]||[];
    var bodyEl=document.getElementById('cal-det-body');
    if(arts.length){{
      var html='<div class="cal-det-arts">';
      arts.forEach(function(a){{
        html+='<a href="'+_esc(a.link)+'" target="_blank" rel="noopener" class="cal-det-art">'
          +_esc(a.title)
          +'<small>'+_esc(a.source)+(a.ago?' · '+a.ago:'')+'</small>'
          +'</a>';
      }});
      html+='</div>';
      bodyEl.innerHTML=html;
    }} else {{
      bodyEl.innerHTML='<p class="cal-det-none">No recent coverage found.</p>';
    }}
    var detEl=document.getElementById('cal-det');
    detEl.style.display='';
    detEl.scrollIntoView({{behavior:'smooth',block:'start'}});
    document.querySelectorAll('.cal-erow.ev-sel').forEach(function(el){{el.classList.remove('ev-sel');}});
    document.querySelectorAll('.cal-erow[data-ev="'+CSS.escape(name)+'"]').forEach(function(el){{
      el.classList.add('ev-sel');
    }});
  }}
  function closeDetail(){{
    activeEv=null;
    document.getElementById('cal-det').style.display='none';
    document.querySelectorAll('.cal-erow.ev-sel').forEach(function(el){{el.classList.remove('ev-sel');}});
  }}
  document.addEventListener('click',function(ev){{
    var row=ev.target.closest('.cal-erow[data-ev]');
    if(row){{
      var name=row.dataset.ev, range=row.dataset.range;
      if(activeEv===name){{closeDetail();}} else {{openDetail(name,range);}}
      return;
    }}
    if(ev.target.closest('#cal-det-close')){{closeDetail();}}
  }});
  document.getElementById('cal-prev').onclick=function(){{if(cur>0){{cur--;show(cur);}}}};
  document.getElementById('cal-next').onclick=function(){{if(cur+getShow()<total){{cur++;show(cur);}}}};
  window.addEventListener('resize',function(){{show(cur);}});
  show(cur);
}})();
</script>"""
    return _sec("#0C0C0C","Competitions &amp; Festivals 2026–27 (OLD)", body)

def build_calendar(event_news={}):
    today     = datetime.now()
    today_str = today.strftime("%Y-%m-%d")

    cat_col = {
        "culture":"#7C3AED","fashion":"#EA580C","football":"#15803D",
        "f1":"#DC2626","horses":"#B45309","swimming":"#1D4ED8",
        "rowing":"#0E7490","sailing":"#0F766E","tennis":"#0284C7",
        "golf":"#166534","cycling":"#D97706","rugby":"#7E22CE",
        "music":"#DB2777","tech":"#0369A1","finance":"#374151",
    }
    cat_lbl = {
        "culture":"Culture","fashion":"Fashion","football":"Football",
        "f1":"F1","horses":"Horses","swimming":"Swimming",
        "rowing":"Rowing","sailing":"Sailing","tennis":"Tennis",
        "golf":"Golf","cycling":"Cycling","rugby":"Rugby",
        "music":"Music","tech":"Tech","finance":"Finance",
    }
    mon_abbr = ["","Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"]

    def fmt_range(s_str, e_str):
        from datetime import datetime as _dt
        s = _dt.strptime(s_str, "%Y-%m-%d")
        e = _dt.strptime(e_str, "%Y-%m-%d")
        if s_str == e_str:
            return f"{s.day} {mon_abbr[s.month]}"
        if s.year == e.year and s.month == e.month:
            return f"{s.day}–{e.day} {mon_abbr[s.month]}"
        if s.year != e.year:
            return f"{s.day} {mon_abbr[s.month]} – {e.day} {mon_abbr[e.month]} {e.year}"
        span = (e - s).days
        if span > 60:
            return f"{mon_abbr[s.month]} – {mon_abbr[e.month]} {s.year}"
        return f"{s.day} {mon_abbr[s.month]} – {e.day} {mon_abbr[e.month]}"

    # Sort: live first → upcoming → past (chronological within each group)
    def _ev_sort(e):
        if e["start"] <= today_str <= e["end"]: return (0, e["start"])
        if e["end"] >= today_str:               return (1, e["start"])
        return                                        (2, e["start"])
    sorted_evs = sorted(CALENDAR_EVENTS, key=_ev_sort)

    # First non-past event (live events now come first, so idx 0 is live or next up)
    scroll_idx = 0
    for i, e in enumerate(sorted_evs):
        if e["end"] >= today_str:
            scroll_idx = i
            break

    cards_html = ""
    for i, e in enumerate(sorted_evs):
        col     = cat_col.get(e["cat"], "#555")
        lbl     = cat_lbl.get(e["cat"], e["cat"])
        rng     = fmt_range(e["start"], e["end"])
        is_live = e["start"] <= today_str <= e["end"]
        is_past = e["end"] < today_str
        cls     = (" ev-live" if is_live else " ev-past" if is_past else "")
        live_badge = '<span class="cal-live-badge">LIVE</span>' if is_live else ""

        arts = event_news.get(e["name"], [])
        # Use first article image as card background if available
        card_img = next((a["img"] for a in arts if a.get("img")), "")
        arts_html = ""
        for a in arts:
            arts_html += (
                f'<a href="{_s(a.get("link","#"))}" target="_blank" rel="noopener" class="cal-det-art">'
                f'<span class="cal-det-art-title">{_s(a.get("title",""))}</span>'
                f'<span class="cal-det-art-meta">{_s(a.get("source",""))}'
                f'{(" · "+_s(a["ago"])) if a.get("ago") else ""}</span>'
                f'</a>'
            )
        if not arts_html:
            arts_html = '<p class="cal-det-none">No recent coverage.</p>'
        search_url = f'https://www.google.com/search?q={_s(e["name"]+" 2026")}'
        # Background: photo if available, otherwise colour gradient
        if card_img:
            bg_style = (f"background-image:url({_s(card_img)});"
                        f"background-size:cover;background-position:center top")
        else:
            bg_style = (f"background:linear-gradient(135deg,{col} 0%,"
                        f"rgba(8,8,8,.97) 65%)")

        cards_html += (
            f'<div class="cal-ev-card{cls}" id="cev-{i}" style="--evc:{col}">'
            f'<div class="cal-ev-bg" style="{bg_style}"></div>'
            f'<div class="cal-ev-body">'
            f'<div class="cal-ev-meta">'
            f'<span class="cal-ev-cat-chip">{_s(lbl)}</span>'
            f'{live_badge}'
            f'</div>'
            f'<div class="cal-ev-name">{_s(e["name"])}</div>'
            f'<div class="cal-ev-range">{_s(rng)}</div>'
            f'</div>'
            f'<div class="cal-ev-panel">'
            f'<div class="cal-ev-panel-hd">Recent Coverage</div>'
            f'{arts_html}'
            f'<a href="{search_url}" target="_blank" rel="noopener" class="cal-search-link">Search Google →</a>'
            f'</div>'
            f'</div>\n'
        )

    body = f"""<div class="cal-band" id="cal-band">{cards_html}</div>
<script>
(function(){{
  var band=document.getElementById('cal-band');
  var all=[].slice.call(band.querySelectorAll('.cal-ev-card'));
  var hoverTimer=null; /* single shared timer — cancelled on any mouseleave */

  /* ── helpers ── */
  function setCenter(card){{
    all.forEach(function(c){{c.classList.remove('ev-center');}});
    if(card)card.classList.add('ev-center');
  }}

  function scrollToCard(card){{
    var target=card.offsetLeft+card.offsetWidth/2-band.clientWidth/2;
    band.scrollTo({{left:Math.max(0,target),behavior:'smooth'}});
  }}

  /* ── hover: only expand+scroll after mouse rests 220ms on a card ── */
  all.forEach(function(card){{
    if(card.classList.contains('ev-past'))return;

    card.addEventListener('mouseenter',function(){{
      clearTimeout(hoverTimer);
      hoverTimer=setTimeout(function(){{
        setCenter(card);
        /* wait for flex expansion to progress, then scroll */
        setTimeout(function(){{scrollToCard(card);}},80);
      }},220);
    }});

    card.addEventListener('mouseleave',function(){{
      /* cancel any pending expand/scroll immediately */
      clearTimeout(hoverTimer);
      if(!card.classList.contains('ev-open')){{
        setCenter(null);
      }}
    }});

    /* ── click: toggle article panel ── */
    card.addEventListener('click',function(){{
      var isOpen=card.classList.contains('ev-open');
      all.forEach(function(c){{c.classList.remove('ev-open');}});
      if(!isOpen){{
        card.classList.add('ev-open');
        setCenter(card);
        setTimeout(function(){{scrollToCard(card);}},80);
      }}
    }});
  }});

  /* ── on load: scroll to first live / upcoming event ── */
  var si=document.getElementById('cev-{scroll_idx}');
  if(si&&!si.classList.contains('ev-past')){{
    setCenter(si);
    setTimeout(function(){{
      var target=si.offsetLeft+si.offsetWidth/2-band.clientWidth/2;
      band.scrollLeft=Math.max(0,target);
    }},120);
  }}
}})();
</script>"""
    return _sec("#0C0C0C","Competitions &amp; Festivals 2026–27", body)

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Morning Brief v4 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("─"*54)
    print("  Loading OpenAI…")
    ai_client     = _load_openai()
    headline_cache = _load_headline_cache()
    print("  Fetching AFP (Telegram)…")
    tg_arts  = _fetch_telegram()
    afp      = _route_afp(tg_arts)
    print("  Fetching Tech/VC…")
    les_echos_tech = _fetch_les_echos(LES_ECHOS_TECH_KW, "tech")
    tech_raw = _cap_per_source(_dedup_exact(_filter_recent(_fetch(TECH_SOURCES) + les_echos_tech + afp["tech"])))
    tech_grp = _dedup(tech_raw)
    print(f"    → {len(tech_raw)} articles → {len(tech_grp)} stories")
    print("  Fetching Macro…")
    les_echos_macro = _fetch_les_echos(LES_ECHOS_MACRO_KW, "macro")
    macro_raw = _cap_per_source(_dedup_exact(_filter_recent(_fetch(MACRO_SOURCES) + les_echos_macro + afp["macro"])))
    macro_grp = _dedup(macro_raw)
    print(f"    → {len(macro_raw)} articles → {len(macro_grp)} stories")
    print("  Fetching Culture/Fashion…")
    art_newspaper_arts = _fetch_art_newspaper(5)
    print(f"    → {len(art_newspaper_arts)} NYT Arts articles (pinned)")
    culture_raw = _dedup_exact(_filter_recent(_fetch(CULTURE_SOURCES)))
    # Prepend NYT Arts articles so they survive dedup and always appear
    seen_links = {a["link"] for a in art_newspaper_arts if a.get("link")}
    culture_arts = art_newspaper_arts + [a for a in culture_raw if a.get("link") not in seen_links]
    print(f"    → {len(culture_arts)} articles total")
    print("  Fetching Sports…")
    sports_raw = _dedup_exact(_filter_recent(
        _fetch(SPORTS_SOURCES_FR) + _fetch(SPORTS_SOURCES_INT)
    ))
    sports_raw.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    sports_grp = _dedup(sports_raw)
    print(f"    → {len(sports_raw)} articles → {len(sports_grp)} stories")
    print("  Fetching conflict news…")
    conflict_pool = _dedup_exact(_filter_recent(_fetch(CONFLICT_NEWS_SOURCES) + afp["conflict"]))
    print(f"    → {len(conflict_pool)} articles for conflict matching")
    print("  Fetching Paris…")
    # Paris What's On: fetch then strip TV/radio show listings
    _PARIS_BLOCKLIST = [re.compile(p, re.IGNORECASE) for p in [
        r"\bBFM\b", r"\bLCI\b", r"\bTF1\b", r"\bFrance\s*[2345]\b",
        r"\bM6\b", r"\bC8\b", r"\bCNews\b", r"semaine \d+",
        r"week.end\s+(tv|radio)", r"direct (tv|bfm|lci)",
        r"matinale", r"grand rendez.vous", r"politique s.éclair",
        r"informés de l.europe", r"vérificateurs", r"franchise",
        r"monacoscope",
    ]]
    def _not_tv(a):
        t = (a.get("title","") or "")
        return not any(p.search(t) for p in _PARIS_BLOCKLIST)
    paris_arts = list(filter(_not_tv, _dedup_exact(_filter_recent(_fetch(PARIS_SOURCES)))))
    print(f"    → {len(paris_arts)} Paris articles")
    print("  Fetching Cities (Marseille & Paris)…")
    cities_raw = _filter_city_local(_dedup_exact(_filter_recent(_fetch(CITIES_SOURCES))))
    cities_grp = _dedup(cities_raw)
    print(f"    → {len(cities_raw)} articles → {len(cities_grp)} stories")
    print("  Fetching Polymarket…")
    poly_markets = _fetch_polymarket()
    print("  Generating AI headlines…")
    tech_grp   = _enrich_groups(tech_grp,   ai_client, headline_cache)
    macro_grp  = _enrich_groups(macro_grp,  ai_client, headline_cache)
    sports_grp = _enrich_groups(sports_grp, ai_client, headline_cache)
    cities_grp = _enrich_groups(cities_grp, ai_client, headline_cache)
    _save_headline_cache(headline_cache)
    print(f"    → {sum(1 for g in tech_grp+macro_grp+sports_grp+cities_grp if len(g)>1)} groups enriched")
    print("  Fetching calendar event news…")
    event_news = _fetch_calendar_event_news()
    print(f"    → {len(event_news)} events with coverage")
    raw_match = _match_conflicts(conflict_pool)
    conf_arts_js = {
        cid: [{"title":a["title"],"link":a["link"],
               "source":a["source"],"ago":_ago(a["date"]),"ts":a["ts"]}
              for a in arts]
        for cid, arts in raw_match.items()
    }
    conf_js = [{k:v for k,v in c.items() if k!="keywords"} for c in CONFLICTS]
    now_paris = datetime.now(_PARIS)
    now_str   = now_paris.strftime("%A %d %B %Y — %H:%M")
    # Count articles published since midnight Paris time
    today_start_ts = now_paris.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    all_arts = (
        list(tech_raw) + list(macro_raw) + list(culture_arts)
        + list(sports_raw) + list(conflict_pool)
        + list(paris_arts) + list(cities_raw)
    )
    new_today = sum(1 for a in all_arts if a.get("ts") and a["ts"] >= today_start_ts)
    new_today_str = f"{new_today} new article{'s' if new_today != 1 else ''} today"
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Morning Brief</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;1,400;1,600&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>{CSS}</style>
</head>
<body>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/topojson-client@3/dist/topojson-client.min.js"></script>

<!-- ① HERO -->
<section class="snap-sec hero-sec">
  <h1 class="hero-h1">Morning<br>Brief</h1>
  <div class="hero-meta">
    <span class="hero-count">{new_today_str}</span>
    <span class="hero-sep">·</span>
    <span class="hero-date-str" id="hero-time">{now_str}</span>
    <button class="btn" onclick="location.reload()" style="margin-left:8px">↻</button>
  </div>
  <span class="hero-hint">Scroll to explore ↓</span>
  <script>
  (function(){{
    function _updateTime(){{
      var el = document.getElementById('hero-time');
      if (!el) return;
      var now = new Date();
      var opts = {{timeZone:'Europe/Paris',weekday:'long',day:'2-digit',month:'long',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}};
      var parts = new Intl.DateTimeFormat('en-GB', opts).formatToParts(now);
      var get = function(t){{ return (parts.find(function(p){{return p.type===t;}})||{{}}).value||''; }};
      el.textContent = get('weekday')+' '+get('day')+' '+get('month')+' '+get('year')+' — '+get('hour')+':'+get('minute');
    }}
    _updateTime();
    setInterval(_updateTime, 30000);
  }})();
  </script>
  {build_ticker(afp["ticker"])}
</section>

<!-- ② GEOPOLITICAL -->
<section class="snap-sec snap-geo">
{build_map(json.dumps(conf_js, ensure_ascii=False),
           json.dumps(conf_arts_js, ensure_ascii=False))}
</section>

<!-- ③ TECH + MACRO -->
<section class="snap-sec snap-feed">
  <div class="two-col">
{build_tech(tech_grp)}
{build_macro(macro_grp)}
  </div>
<div class="snap-feed-bottom">
{build_polymarket_band(poly_markets)}
{build_price_band()}
</div>
</section>

<!-- ④ CULTURE + EVENTS -->
<section class="snap-sec snap-culture">
{build_culture(culture_arts, event_news)}
</section>

<!-- ⑤ SPORTS + CITIES + PARIS -->
<section class="snap-sec snap-bottom">
  <div class="three-col">
{build_sports(sports_grp)}
{build_cities(cities_grp)}
{build_paris(paris_arts)}
  </div>
</section>

</body>
</html>"""
    OUTPUT_FILE.write_text(page, encoding="utf-8")
    print("─"*54)
    print(f"✓  Saved → {OUTPUT_FILE}")
if __name__ == "__main__":
    main()
