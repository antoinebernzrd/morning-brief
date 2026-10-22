"""Source lists, keyword sets and per-source rules for the brief. Pure configuration."""
#!/usr/bin/env python3
"""Morning Brief v4"""
import base64
import hashlib
import random
import calendar as _cal
import feedparser
import html as html_lib
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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
OUTPUT_DIR = Path(__file__).parent

MAX_PER_SOURCE = 100   # effectively uncapped — 24h filter does the work
# Sources that publish weekly or less — get a 7-day window instead of 24h
WEEKLY_SOURCES = frozenset([
    "Not Boring", "Silicon Carne", "TBPN",
    "Dezeen", "The Ankler",
    # Listed for other callers' sake, but the geo panel passes weekly_days=2,
    # so Playbook Paris is held to the same 48h as the rest of that section and
    # simply drops out while it pauses for French August.
    "Playbook Paris",
])
# Map sub-feeds to their canonical publication name for exact-dupe collapsing
# Le Grand Continent publishes by rubrique. The site splits into two halves for
# our purposes: the argued pieces read as Opinions, the reported ones as
# Geopolitics. Each keeps the masthead and carries its rubrique as the beat.
LGC_RUBRIQUES = {
    # source name            (rubrique path,        beat label)
    "LGC Entretiens":        ("entretiens",         "Entretiens"),
    "LGC Comptes rendus":    ("comptes-rendus",     "Compte rendu"),
    "LGC Doctrine":          ("doctrine",           "Pièces de doctrines"),
    "LGC Histoire":          ("histoire-en-images", "Histoire en image"),
    "LGC Discours":          ("discours",           "Archives et discours"),
    "LGC Études":            ("etudes",             "Études"),
    # "Perspectives sur l'actualité" (actu-longues / actu-breves) is no longer
    # free, so both rubriques are dropped.
}
LGC_OPINION = ("LGC Entretiens", "LGC Comptes rendus", "LGC Doctrine", "LGC Histoire")
LGC_GEO     = ("LGC Discours", "LGC Études")

def _lgc_feeds(names):
    return [(n, f"https://legrandcontinent.eu/fr/{LGC_RUBRIQUES[n][0]}/feed/")
            for n in names]

LGC_GEO_SOURCES     = _lgc_feeds(LGC_GEO)
LGC_OPINION_SOURCES = _lgc_feeds(LGC_OPINION)
LGC_BEAT = {n: b for n, (_, b) in LGC_RUBRIQUES.items()}

SOURCE_CANONICAL = {
    **{n: "Le Grand Continent" for n in LGC_RUBRIQUES},
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
    # Added
    ("MTS Newsletter",      "https://mtslive.substack.com/feed"),
    ("Stratechery",         "https://stratechery.com/feed/"),
    ("Scott Aaronson",      "https://scottaaronson.blog/?feed=rss2"),
    ("TechCrunch",          "https://techcrunch.com/feed/"),
    ("First Round Review",  "https://news.google.com/rss/search?q=site:review.firstround.com&hl=en&gl=US&ceid=US:en"),
    ("Lenny's Newsletter",  "https://www.lennysnewsletter.com/feed"),
    ("Pragmatic Engineer",  "https://newsletter.pragmaticengineer.com/feed"),
]
# Pinned to the top of the Private Markets list
TECH_PINNED = ("MTS Newsletter",)

# How often a source publishes. Drives which of the three bands an article
# lands in on the Markets page: the day's debriefs on top, the fast wire in
# the middle, the occasional essayists along the bottom.
SOURCE_CADENCE = {
    # once a day, read these first
    "MTS Newsletter":"daily", "Stratechery":"slow", "The NBS":"daily",
    "FirstFT":"daily", "The Block Daily":"daily",     "Howard Marks":"slow", "Musings on Markets":"slow", "Citation Needed":"slow",
    "The War Room":"slow",
    **{n: "slow" for n in LGC_RUBRIQUES},
    # several times a day
    "TechCrunch":"fast", "FT Tech":"fast", "FT Companies Tech":"fast", "FT":"fast",
    "The Block":"fast", "The Street":"fast",
    "Les Echos":"fast", "Les Echos tech":"fast", "Les Echos macro":"fast",
    "tech":"fast", "macro":"fast", "AFP":"fast",
    # every week or two, or rarer
    "Not Boring":"slow", "Silicon Carne":"slow", "TBPN":"slow",
    "First Round Review":"slow", "Lenny's Newsletter":"slow",
    "Pragmatic Engineer":"slow", "Scott Aaronson":"slow",
    "Bits About Money":"slow", "Reaction Wheel":"slow",
}
DEFAULT_CADENCE = "fast"

# Rare, evergreen essayists: both go months between posts, so a recency window
# would hide them almost permanently. Fetched separately and always shown, the
# way NYT Arts is in Culture.
TECH_EVERGREEN = [
    ("Bits About Money", "https://www.bitsaboutmoney.com/archive/rss/"),
    ("Reaction Wheel",   "https://reactionwheel.net/feed"),
]

# Per-source recency windows (days). Anything absent uses the section default.
SOURCE_WINDOW_DAYS = {"Howard Marks":365, "The War Room":60,
                      **{n: 14 for n in LGC_RUBRIQUES}, "Musings on Markets":45,
                      "Citation Needed":21,     "Scott Aaronson":    14,   # ~2 posts a week
    "Film Comment":      30,   # publishes in bursts, then goes quiet for weeks
    "Common Edge":        7,   # near-daily
    "Works in Progress": 14,   # ~weekly
}

MACRO_SOURCES = [
    # ── Public finance (the "Public Finance" button) ──────────────────────────
    # FirstFT is the FT's daily debrief — pinned to the top of the list.
    ("FirstFT",         "https://www.ft.com/firstft?format=rss"),
    ("The Street",      "https://news.google.com/rss/search?q=site:thestreet.com&hl=en&gl=US&ceid=US:en"),
    # The stack: Oaktree publishes no feed at all (every rss path 404s), so
    # Marks comes through Google News; Damodaran's blogspot feed is direct.
    ("Howard Marks",    "https://news.google.com/rss/search?q=site:oaktreecapital.com%2Finsights%2Fmemo+when:365d&hl=en&gl=US&ceid=US:en"),
    ("Musings on Markets", "https://aswathdamodaran.blogspot.com/feeds/posts/default?alt=rss"),
    # Les Echos fetched separately via _fetch_les_echos_macro() — Python-side keyword filtering
    # ── Crypto (the "Crypto" button) ─────────────────────────────────────────
    ("The Block",       "https://www.theblock.co/rss.xml"),
    # The Block's daily debrief has no feed of its own and its site returns 403
    # to scripts; its newsletters are only reachable through Google News, mixed
    # in with their other titles (The Funding, Data & Insights, Layer One).
    # _keep_block_daily() narrows this back down to "The Daily" issues.
    ("The Block Daily", "https://news.google.com/rss/search?q=site:theblock.co/newsletters+when:14d&hl=en&gl=US&ceid=US:en"),
    ("Citation Needed", "https://www.citationneeded.news/rss/"),
]
# ── Geopolitics side panel ────────────────────────────────────────────────────
# Politico publishes proper RSS for its sections *and* its newsletters, so
# Playbook Paris needs no email subscription — same trick as The Block Daily,
# just with a real feed instead of a Google News search.
GEO_SOURCES = [
    # The Economist's daily World in Brief. Its own feed 403s and economist.com
    # blocks scripts at the Cloudflare edge, so Google News is the way in — and
    # that search also returns non-brief Economist pieces, hence _keep_world_brief().
    ("World in Brief",  "https://news.google.com/rss/search?q=site:economist.com/the-world-in-brief+when:7d&hl=en&gl=US&ceid=US:en"),
    ("Playbook Paris",  "https://www.politico.eu/newsletter/playbook-paris/feed/"),
    ("Politico France", "https://www.politico.eu/country/france/feed/"),
    ("Politico EU",     "https://www.politico.eu/feed/"),
    # The Economist's defence newsletter. It is subscriber-only by email, but
    # each issue is also published as an article and filed under whichever
    # region it covers — so it turns up in the public section feeds, current
    # issue included. The topic page itself (/topics/military-history) and every
    # feed under it answer 403. _keep_war_room() keeps only the newsletter
    # issues out of these sections.
    *[("The War Room", f"https://www.economist.com/{sec}/rss.xml")
      for sec in ("international", "europe", "united-states",
                  "middle-east-and-africa", "asia", "china", "britain")],
    # The long-form tier: Politico is a wire, this is the essay side. Le Grand
    # Continent is taken rubrique by rubrique rather than through its main
    # feed, whose categories are topical and don't say what kind of piece it is.
    *LGC_GEO_SOURCES,
]
# Daily debriefs — pinned to the top of the panel, World in Brief first
GEO_PINNED = ("World in Brief", "Playbook Paris")

_GN_SUFFIX = re.compile(r"\s*[-–—]\s*(?:Bloomberg\.com|Bloomberg|"
                        r"Oaktree Capital Management|Oaktree Capital|"
                        r"TheStreet|The Street|The Block)\s*$", re.I)

def _drop_audio_dupes(arts):
    """Oaktree posts each memo twice — once to read, once to listen to. The
    audio page carries the same title with "(Audio)" appended."""
    return [a for a in arts if not re.search(r"\(\s*audio\s*\)\s*$",
                                             _GN_SUFFIX.sub("", a["title"]).strip(), re.I)]

def _strip_gn_suffix(arts):
    """Google News tacks " - Publisher" onto every headline it returns. The
    source is already printed above the title, so the suffix is just noise."""
    for a in arts:
        a["title"] = _GN_SUFFIX.sub("", a["title"]).strip()
    return arts

def _keep_war_room(arts):
    """The section feeds carry everything in that region. Keep only the War Room
    newsletter issues, and drop the prefix — the source is printed above it.
    An issue can be filed under two regions, so repeats are dropped by link."""
    out, seen = [], set()
    for a in arts:
        if a["source"] != "The War Room":
            out.append(a)
            continue
        if a["link"] in seen:
            continue
        t = re.sub(r"\s*-\s*The Economist\s*$", "", a["title"]).strip()
        m = re.match(r"^The War Room newsletter\s*:\s*(.+)$", t, re.I)
        if not m:
            continue
        seen.add(a["link"])
        a["title"] = m.group(1).strip()
        out.append(a)
    return out

def _keep_world_brief(arts):
    """The World in Brief feed is a Google News search over the whole section,
    which also surfaces ordinary Economist articles. Keep only the briefs."""
    out = []
    for a in arts:
        if a["source"] != "World in Brief":
            out.append(a)
            continue
        title = re.sub(r"\s*-\s*The Economist\s*$", "", a["title"]).strip()
        if not title.lower().startswith("world in brief"):
            continue
        a["title"] = title
        out.append(a)
    return out

# Printed next to the source, the way Culture names its beat
MACRO_BEAT = {
    "FirstFT":"Public Finance", "The Street":"Public Finance",
    "Howard Marks":"Public Finance", "Musings on Markets":"Public Finance",
    "AFP":"Public Finance", "Les Echos":"Public Finance",
    "The Block":"Crypto", "The Block Daily":"Crypto",
    "Citation Needed":"Crypto",
}
# Which button each macro source sits behind
MACRO_CATEGORY = {
    "FirstFT":         "finance",
    "The Street":      "finance",
    "Howard Marks":    "finance",
    "Musings on Markets": "finance",
    "Les Echos":       "finance",
    "Les Echos macro": "finance",
    "AFP":             "finance",
    "The Block":       "crypto",
    "The Block Daily": "crypto",
    "Citation Needed": "crypto",
}
# Daily debriefs — always sorted to the top of the macro list
MACRO_PINNED = ("FirstFT", "The Block Daily")

def _keep_block_daily(arts):
    """The Block Daily's feed is a Google News search across *all* The Block
    newsletters. Keep only 'The Daily' issues and tidy the GN title suffix."""
    out = []
    for a in arts:
        if a["source"] != "The Block Daily":
            out.append(a)
            continue
        title = re.sub(r"\s*-\s*theblock\.co\s*$", "", a["title"]).strip()
        if not title.lower().startswith("the daily"):
            continue
        a["title"] = title
        out.append(a)
    return out
CULTURE_SOURCES = [
    ("Dezeen",            "https://www.dezeen.com/feed/"),
    # Télérama publishes proper section feeds — every item carries an image,
    # unlike the Google News proxy which carries none.
    ("Télérama Cinéma",   "https://www.telerama.fr/rss/cinema.xml"),
    ("Télérama Séries",   "https://www.telerama.fr/rss/series-tv.xml"),
    # The Ankler — Richard Rushfield's column specifically
    ("The Ankler",        "https://theankler.com/richard-rushfield/feed"),
    # artnet's feed carries no images and its article pages 403 any script,
    # so these render as colour tiles.
    ("Artnet",            "https://news.artnet.com/feed"),
    # Neither carries a feed image, but both expose og:image on the article
    # page, so _backfill_images() gives them real pictures.
    ("Film Comment",      "https://www.filmcomment.com/feed/"),
    ("Common Edge",       "https://commonedge.org/feed/"),
]
# Each Culture source is a beat, so the tag needs no guesswork.
CULTURE_BEAT = {
    "Dezeen":"Architecture", "Common Edge":"Architecture",
    "Télérama Cinéma":"Cinema", "Film Comment":"Cinema", "The Ankler":"Cinema",
    "Télérama Séries":"Series",
    "NSS Magazine":"Fashion",
    "Artnet":"Arts", "The NYT Arts":"Arts",
}
# Sources that publish essays rather than items — their cards run taller.
CULTURE_LONG_READ = ("The Ankler", "Common Edge", "Film Comment")

# Colour used for a culture tile when no image can be found
CULTURE_SOURCE_COLORS = {
    "Dezeen":            "#1C1C1E",
    "Télérama Cinéma":   "#7A1F3D",
    "Télérama Séries":   "#123A5C",
    "The Ankler":        "#7A3B00",
    "Artnet":            "#1F3A5F",
    "Film Comment":      "#3B1F1F",   # deep maroon
    "Common Edge":       "#33403B",   # slate green
    "The NYT Arts":      "#2A2A2E",
}
# NYT Arts is fetched separately and always pinned (5 latest guaranteed)
ART_NEWSPAPER_FEED = "https://rss.nytimes.com/services/xml/rss/nyt/Arts.xml"

# ── Gossip page sources ───────────────────────────────────────────────────────
# Les Echos Politique/Idées handled via _fetch_les_echos keyword filter (see main())
# Other sources: direct RSS where available, Google News with when:14d as fallback
# Dropped 2026-08: Le Monde Diplo, Le Canard, Franc-Tireur, Le 1 Hebdo.
# (Le 1 Hebdo's RSS is dead — every documented feed path 404s.)
GOSSIP_SOURCES_OTHER = [
    # 229 posts and fresh, but not one carries an image — renders as colour tiles
    ("Works in Progress","https://worksinprogress.co/rss.xml"),
    # The Economist opinion = Leaders (editorials) + By Invitation (guest essays).
    # Feeds carry no images and article pages sit behind a Cloudflare challenge,
    # so these render as designed colour tiles (see .gos-noimg).
    ("The Economist",    "https://www.economist.com/leaders/rss.xml"),
    ("The Economist",    "https://www.economist.com/by-invitation/rss.xml"),
    # Technology read philosophically rather than as risk-versus-hype.
    ("The Convivial Society", "https://theconvivialsociety.substack.com/feed"),
    ("Noema",            "https://www.noemamag.com/feed/"),
    ("Programmable Mutter", "https://www.programmablemutter.com/feed"),
    # Quarterly, and the only one of the four with no image in its feed — but
    # every article page carries an og:image, so the backfill gets them.
    ("The New Atlantis", "https://www.thenewatlantis.com/feed"),
    *LGC_OPINION_SOURCES,
]
# Per-source time window in days
GOSSIP_WINDOW_DAYS = {
    "The Economist":     4,   # weekly print cadence → 4 days
    "Works in Progress": 14,  # ~weekly
    "The Convivial Society": 45,   # roughly monthly
    "Noema":             21,
    "Programmable Mutter": 21,
    "The New Atlantis":  150,      # quarterly print cadence
    **{n: 14 for n in LGC_OPINION},
}
# Colour for each source's badge chip (also drives the no-image tile)
GOSSIP_SOURCE_COLORS = {
    "The Economist":    "#E3120B",   # Economist red
    "Works in Progress":"#2F4858",   # slate
    "Le Grand Continent":"#12324A",
    "The Convivial Society":"#3B2F1E",
    "Noema":"#1C2E3A",
    "Programmable Mutter":"#2B2340",
    "The New Atlantis":"#2A3524",
}
MACRO_SOURCE_COLORS = {
    "Howard Marks":"#123A2E",
    "Musings on Markets":"#3B2A55", "Citation Needed":"#4A2A2A",
    "FirstFT":"#3A2A22", "The Block Daily":"#12324A", "The Street":"#1F2A24",
}
# L'Équipe retired its RSS — every lequipe.fr/rss/ path now 404s, and RMC and
# Eurosport return nothing. Google News is the only remaining route to L'Équipe
# and it carries no usable image, so the French side is carried by Le Monde and
# France Info, both of which ship a picture on every item.
SPORTS_SOURCES_FR = [
    ("Le Monde Sport",  "https://www.lemonde.fr/sport/rss_full.xml"),
    ("France Info Sport", "https://www.francetvinfo.fr/sports.rss"),
    ("L'Équipe GN",     "https://news.google.com/rss/search?q=site:lequipe.fr+when:2d&hl=fr&gl=FR&ceid=FR:fr"),
]
SPORTS_SOURCES_INT = [
    ("BBC Sport",     "https://feeds.bbci.co.uk/sport/rss.xml"),
    ("BBC Football",  "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("Sky Sports",    "https://www.skysports.com/rss/12040"),
    ("Guardian Sport","https://www.theguardian.com/uk/sport/rss"),
]
CONFLICT_NEWS_SOURCES = [
    # ── Broad wire / world feeds ──────────────────────────────────────────────
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
    # Google News fallbacks — topic-specific so any source can surface
    ("Expos Paris",    "https://news.google.com/rss/search?q=exposition+paris+mus%C3%A9e+OR+galerie+OR+vernissage&hl=fr&gl=FR&ceid=FR:fr"),
    ("Sorties Paris",  "https://news.google.com/rss/search?q=agenda+paris+concert+OR+th%C3%A9%C3%A2tre+OR+spectacle+OR+danse+OR+ballet&hl=fr&gl=FR&ceid=FR:fr"),
]
CITIES_SOURCES = [
    # Marseille — curated city section + local investigative
    ("Le Monde Marseille", "https://www.lemonde.fr/marseille/rss_full.xml"),
    ("Marsactu",           "https://marsactu.fr/feed/"),
    ("BFM Marseille",      "https://www.bfmtv.com/marseille/rss/une.xml"),
    # Paris — curated city section + regional daily
    ("Le Monde Paris",     "https://www.lemonde.fr/paris/rss_full.xml"),
    ("Le Parisien",        "https://news.google.com/rss/search?q=%22Paris%22+%22arrondissement%22+OR+%22Seine%22+OR+%22Île-de-France%22+site:leparisien.fr&hl=fr&gl=FR&ceid=FR:fr"),
]
# For build_cities: identify which sources are Marseille vs Paris
MARSEILLE_SOURCE_NAMES = {"Le Monde Marseille", "Marsactu", "BFM Marseille"}
# ══════════════════════════════════════════════════════════════════════════════
#  CALENDAR
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
#  The calendar used to be 93 hand-typed dated entries. Checked against
#  Wikidata, 12 of the 19 that could be verified were wrong — G20 a month off,
#  World Rowing two weeks early, Venice a week early (so it showed as over
#  while it was still running) — and the list simply ran out after 2027.
#
#  Each event is now a recurring SERIES. Where Wikidata holds an edition, its
#  item ID seeds a walk along "followed by" (P156), so the next edition and its
#  official dates are found without anyone typing them. One SPARQL request per
#  build fetches every series at once. `est` is the usual window, used both as
#  the fallback and as a plausibility gate: a looked-up date more than 60 days
#  from it is rejected, which is how a title that resolved to a whole F1
#  season was caught. Fashion weeks, CES, the UN General Assembly and a few
#  others have no dated editions on Wikidata; they stay estimated, and the
#  page shows them by month rather than pretending to a day.
# ══════════════════════════════════════════════════════════════════════════════
# (name, category, (est start MM-DD, est end MM-DD), wikidata seed or None, every N years, anchor year)
CALENDAR_SERIES = [
    ("CES Las Vegas",               "tech",     ("01-06","01-09"), None,         1, None),
    ("Davos / WEF",                 "finance",  ("01-19","01-23"), None,         1, None),
    ("Australian Open",             "tennis",   ("01-19","02-01"), "Q131917667", 1, None),
    ("Haute Couture SS",            "fashion",  ("01-26","01-30"), None,         1, None),
    ("Super Bowl",                  "football", ("02-08","02-08"), "Q118876146", 1, None),
    ("Six Nations Rugby",           "rugby",    ("02-05","03-14"), "Q134693359", 1, None),
    ("NY Fashion Week FW",          "fashion",  ("02-06","02-11"), None,         1, None),
    ("Berlinale",                   "culture",  ("02-12","02-22"), "Q134698151", 1, None),
    ("London Fashion Week FW",      "fashion",  ("02-13","02-17"), None,         1, None),
    ("Milan Fashion Week FW",       "fashion",  ("02-17","02-23"), None,         1, None),
    ("MWC Barcelona",               "tech",     ("02-23","02-26"), None,         1, None),
    ("Paris Fashion Week FW",       "fashion",  ("02-24","03-03"), None,         1, None),
    ("F1 Season",                   "f1",       ("03-08","12-06"), "Q115942640", 1, None),
    ("Art Paris",                   "culture",  ("04-02","04-05"), None,         1, None),
    ("The Masters",                 "golf",     ("04-09","04-12"), "Q138114512", 1, None),
    ("Coachella",                   "music",    ("04-10","04-19"), "Q136406271", 1, None),
    ("Grand National",              "horses",   ("04-11","04-11"), "Q139254824", 1, None),
    ("Venice Biennale",             "culture",  ("04-18","11-22"), "Q131380247", 2, 2026),
    ("Met Gala",                    "fashion",  ("05-04","05-04"), None,         1, None),
    ("Frieze New York",             "culture",  ("05-07","05-11"), None,         1, None),
    ("Cannes Film Festival",        "culture",  ("05-12","05-23"), "Q138481168", 1, None),
    ("UEFA Europa League Final",    "football", ("05-20","05-20"), "Q126042993", 1, None),
    ("Roland Garros",               "tennis",   ("05-24","06-07"), "Q134821363", 1, None),
    ("UEFA Champions League Final", "football", ("05-30","05-30"), "Q126044615", 1, None),
    ("F1 Monaco GP",                "f1",       ("06-05","06-07"), "Q131177738", 1, None),
    ("Epsom Derby",                 "horses",   ("06-06","06-06"), "Q139407010", 1, None),
    ("Prix du Jockey Club",         "horses",   ("06-07","06-07"), None,         1, None),
    ("FIFA World Cup",              "football", ("06-11","07-19"), "Q5020214",   4, 2026),
    ("Vivatech Paris",              "tech",     ("06-11","06-14"), None,         1, None),
    ("G7 Summit",                   "finance",  ("06-15","06-17"), "Q134977918", 1, None),
    ("Royal Ascot",                 "horses",   ("06-16","06-20"), None,         1, None),
    ("Art Basel",                   "culture",  ("06-17","06-21"), None,         1, None),
    ("US Open Golf",                "golf",     ("06-18","06-21"), "Q136513751", 1, None),
    ("Paris Men's Fashion Week",    "fashion",  ("06-23","06-28"), None,         1, None),
    ("Glastonbury",                 "music",    ("06-24","06-28"), None,         1, None),
    ("Wimbledon",                   "tennis",   ("06-29","07-12"), "Q135334216", 1, None),
    ("Henley Royal Regatta",        "rowing",   ("06-30","07-04"), None,         1, None),
    ("F1 British GP (Silverstone)", "f1",       ("07-03","07-05"), "Q131193692", 1, None),
    ("Tour de France",              "cycling",  ("07-04","07-26"), "Q126721429", 1, None),
    ("Haute Couture FW",            "fashion",  ("07-06","07-10"), None,         1, None),
    ("The Open Championship",       "golf",     ("07-15","07-19"), "Q136841866", 1, None),
    ("Rolex Fastnet Race",          "sailing",  ("07-25","07-31"), None,         2, 2025),
    ("World Rowing Champs",         "rowing",   ("08-24","08-30"), "Q118207536", 1, None),
    ("US Open Tennis",              "tennis",   ("08-31","09-13"), "Q136159486", 1, None),
    ("Venice Film Festival",        "culture",  ("09-02","09-12"), "Q138684025", 1, None),
    ("F1 Italian GP (Monza)",       "f1",       ("09-04","09-06"), "Q134821695", 1, None),
    ("NY Fashion Week SS",          "fashion",  ("09-05","09-11"), None,         1, None),
    ("TIFF Toronto",                "culture",  ("09-10","09-20"), "Q139801915", 1, None),
    ("London Fashion Week SS",      "fashion",  ("09-12","09-16"), None,         1, None),
    ("UN General Assembly",         "finance",  ("09-15","09-25"), None,         1, None),
    ("Milan Fashion Week SS",       "fashion",  ("09-16","09-22"), None,         1, None),
    ("Paris Fashion Week SS",       "fashion",  ("09-28","10-06"), None,         1, None),
    ("Prix de l'Arc de Triomphe",   "horses",   ("10-04","10-04"), None,         1, None),
    ("Frieze London",               "culture",  ("10-14","10-18"), None,         1, None),
    ("Web Summit",                  "tech",     ("11-02","11-05"), None,         1, None),
    ("Paris Photo",                 "culture",  ("11-12","11-15"), None,         1, None),
    ("F1 Abu Dhabi GP",             "f1",       ("12-04","12-06"), None,         1, None),
    ("Art Basel Miami",             "culture",  ("12-04","12-06"), None,         1, None),
    ("G20 Summit",                  "finance",  ("12-14","12-15"), "Q136595417", 1, None),
]

_WIKIDATA_UA = {"User-Agent": "MorningBrief/1.0 (personal news dashboard; event calendar)",
                "Accept": "application/sparql-results+json"}
