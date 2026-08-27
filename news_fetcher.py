#!/usr/bin/env python3
"""Morning Brief v4"""
import base64
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
OUTPUT_DIR          = Path(__file__).parent
OUTPUT_FILE         = OUTPUT_DIR / "index.html"
CONFIG_FILE         = OUTPUT_DIR / "telegram_config.json"
SESSION_FILE        = OUTPUT_DIR / "telegram_session"
OPENAI_CONFIG_FILE  = OUTPUT_DIR / "openai_config.json"
HEADLINE_CACHE_FILE = OUTPUT_DIR / "headline_cache.json"
# feedparser's own fetcher takes no timeout argument, so a black-holed host
# leaves the build wedged in SYN_SENT indefinitely. A process-wide default
# bounds every socket, including the ones we do not open ourselves.
socket.setdefaulttimeout(25)

# Prefer IPv4. Networks that advertise IPv6 but drop the traffic (some French
# ISPs, plenty of cafe wifi) leave every connection sitting in SYN_SENT until
# it times out — browsers hide this with Happy Eyeballs, urllib does not, and
# one such host per feed is enough to stretch a build into the tens of minutes.
# Every host we fetch publishes an A record, so we sort IPv4 first and keep the
# IPv6 results as a fallback rather than dropping them.
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_first(*args, **kwargs):
    res = _orig_getaddrinfo(*args, **kwargs)
    return sorted(res, key=lambda r: 0 if r[0] == socket.AF_INET else 1)
socket.getaddrinfo = _ipv4_first

POLY_CACHE_FILE     = OUTPUT_DIR / "poly_cache.json"
MAX_PER_SOURCE = 100   # effectively uncapped — 24h filter does the work
# Sources that publish weekly or less — get a 7-day window instead of 24h
WEEKLY_SOURCES = frozenset([
    "Not Boring", "Silicon Carne", "TBPN", "SiliconMania",
    "Dezeen", "The Ankler",
    # Listed for other callers' sake, but the geo panel passes weekly_days=2,
    # so Playbook Paris is held to the same 48h as the rest of that section and
    # simply drops out while it pauses for French August.
    "Playbook Paris",
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
    "MTS Newsletter":"daily", "Stratechery":"daily",
    "FirstFT":"daily", "The Block Daily":"daily",
    # several times a day
    "TechCrunch":"fast", "FT Tech":"fast", "FT Companies Tech":"fast", "FT":"fast",
    "The Block":"fast", "The Street":"fast", "The NBS":"fast", "SiliconMania":"fast",
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
SOURCE_WINDOW_DAYS = {
    "Scott Aaronson":    14,   # ~2 posts a week
    "Film Comment":      30,   # publishes in bursts, then goes quiet for weeks
    "Common Edge":        7,   # near-daily
    "Works in Progress": 14,   # ~weekly
}

MACRO_SOURCES = [
    # ── Public finance (the "Public Finance" button) ──────────────────────────
    # FirstFT is the FT's daily debrief — pinned to the top of the list.
    ("FirstFT",         "https://www.ft.com/firstft?format=rss"),
    ("The Street",      "https://news.google.com/rss/search?q=site:thestreet.com&hl=en&gl=US&ceid=US:en"),
    # Les Echos fetched separately via _fetch_les_echos_macro() — Python-side keyword filtering
    # ── Crypto (the "Crypto" button) ─────────────────────────────────────────
    ("The Block",       "https://www.theblock.co/rss.xml"),
    # The Block's daily debrief has no feed of its own and its site returns 403
    # to scripts; its newsletters are only reachable through Google News, mixed
    # in with their other titles (The Funding, Data & Insights, Layer One).
    # _keep_block_daily() narrows this back down to "The Daily" issues.
    ("The Block Daily", "https://news.google.com/rss/search?q=site:theblock.co/newsletters+when:14d&hl=en&gl=US&ceid=US:en"),
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
]
# Daily debriefs — pinned to the top of the panel, World in Brief first
GEO_PINNED = ("World in Brief", "Playbook Paris")

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

# Which button each macro source sits behind
MACRO_CATEGORY = {
    "FirstFT":         "finance",
    "The Street":      "finance",
    "Les Echos":       "finance",
    "Les Echos macro": "finance",
    "AFP":             "finance",
    "The Block":       "crypto",
    "The Block Daily": "crypto",
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
    # Les Echos "Idées & Débats" — the section feed itself. Keyword-filtering
    # the general Les Echos feed matched only ~9 items a day, most of them
    # evergreen topic index pages; this returns the actual opinion pieces.
    ("Les Echos Idées",  "https://news.google.com/rss/search?q=site:lesechos.fr/idees-debats+when:7d&hl=fr&gl=FR&ceid=FR:fr"),
    # The Free Press — direct RSS (daily), carries real article images
    ("The Free Press",   "https://www.thefp.com/feed"),
    # 229 posts and fresh, but not one carries an image — renders as colour tiles
    ("Works in Progress","https://worksinprogress.co/rss.xml"),
    # The Economist opinion = Leaders (editorials) + By Invitation (guest essays).
    # Feeds carry no images and article pages sit behind a Cloudflare challenge,
    # so these render as designed colour tiles (see .gos-noimg).
    ("The Economist",    "https://www.economist.com/leaders/rss.xml"),
    ("The Economist",    "https://www.economist.com/by-invitation/rss.xml"),
]
# Per-source time window in days
GOSSIP_WINDOW_DAYS = {
    "Les Echos Idées":   4,   # section feed → 4 days
    "The Free Press":    2,   # daily → 48h
    "The Economist":     4,   # weekly print cadence → 4 days
    "Works in Progress": 14,  # ~weekly
}
# Colour for each source's badge chip (also drives the no-image tile)
GOSSIP_SOURCE_COLORS = {
    "Les Echos Idées":  "#C84B00",   # burnt orange
    "The Free Press":   "#1D4E3F",   # dark green
    "The Economist":    "#E3120B",   # Economist red
    "Works in Progress":"#2F4858",   # slate
}
SPORTS_SOURCES_FR = [
    # Direct L'Équipe RSS feeds — much faster than Google News indexing
    ("L'Équipe",        "https://www.lequipe.fr/rss/actu_rss.xml"),
    ("L'Équipe Tennis", "https://www.lequipe.fr/rss/actu_rss_Tennis.xml"),
    ("L'Équipe Foot",   "https://www.lequipe.fr/rss/actu_rss_Football.xml"),
    ("L'Équipe F1",     "https://www.lequipe.fr/rss/actu_rss_Formule1.xml"),
    # Google News as backup for broader coverage
    ("L'Équipe GN",     "https://news.google.com/rss/search?q=site:lequipe.fr+when:2d&hl=fr&gl=FR&ceid=FR:fr"),
    ("RMC Sport",       "https://rmcsport.bfmtv.com/rss/infos.xml"),
]
SPORTS_SOURCES_INT = [
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml"),
    ("Eurosport", "https://news.google.com/rss/search?q=site:eurosport.com+when:2d&hl=en&gl=US&ceid=US:en"),
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
_OG_RE = (
    re.compile(r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::url)?["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)', re.I),
)
# Hosts that never yield a usable og:image: Google News wraps articles behind a JS
# redirect (its og:image is a Google logo), and these sit behind a Cloudflare
# challenge that returns 403 to any script. Don't waste build time on them.
_OG_SKIP_HOSTS = ("news.google.com", "economist.com")

def _og_image(url, timeout=8):
    """Scrape an article page for its og:image. Returns "" on any failure —
    callers fall back to the designed colour tile."""
    if not url or not url.startswith("http"):
        return ""
    if any(h in url for h in _OG_SKIP_HOSTS):
        return ""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _FEED_UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "html" not in r.headers.get("Content-Type", ""):
                return ""
            head = r.read(180_000).decode(
                r.headers.get_content_charset() or "utf-8", "ignore")
    except Exception:
        return ""
    for pat in _OG_RE:
        m = pat.search(head)
        if m:
            img = html_lib.unescape(m.group(1).strip())
            if img.startswith("//"):
                img = "https:" + img
            if img.startswith("http"):
                return img
    return ""

def _backfill_images(arts, limit=25):
    """Fill in missing images by scraping og:image, newest first and bounded so a
    slow site can't stall the build. Articles still without an image afterwards
    render as colour tiles."""
    todo = [a for a in arts if not a.get("img")][:limit]
    if not todo:
        return arts
    done = 0
    for a in todo:
        img = _og_image(a.get("link", ""))
        if img:
            a["img"] = img
            done += 1
    print(f"    → og:image backfill: {done}/{len(todo)} recovered")
    return arts

def _filter_recent(arts, days=2, weekly_days=7):
    """Keep articles from the last `days` days. SOURCE_WINDOW_DAYS overrides that
    per source; WEEKLY_SOURCES falls back to `weekly_days`."""
    now_ts = datetime.now(timezone.utc).timestamp()
    result = []
    for a in arts:
        if not a["ts"]:          # no date → keep
            result.append(a)
            continue
        src = a["source"]
        if src in SOURCE_WINDOW_DAYS:
            window = SOURCE_WINDOW_DAYS[src]
        elif src in WEEKLY_SOURCES:
            window = weekly_days
        else:
            window = days
        if a["ts"] >= now_ts - window * 86400:
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
    TRUSTED_LOCAL = {"Marsactu", "Le Monde Marseille", "Le Monde Paris", "BFM Marseille"}
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

def _resolve_gnews(url):
    """No-op: modern Google News links (CBMi… protobuf) can't be decoded offline,
    and resolving them via network costs ~5s each → 10min runs. Links stay wrapped
    (they still work when clicked, just redirect through Google)."""
    return url

_FEED_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Cloudflare blocks *.substack.com for datacenter IPs (GitHub Actions) — if the
# direct fetch comes back empty there, retry via the Google News RSS proxy.
FEED_FALLBACKS = {
    "Silicon Carne":  "https://news.google.com/rss/search?q=site:siliconcarne.substack.com&hl=en&gl=US&ceid=US:en",
    "TBPN":           "https://news.google.com/rss/search?q=site:tbpn.substack.com&hl=en&gl=US&ceid=US:en",
    "MTS Newsletter": "https://news.google.com/rss/search?q=site:mtslive.substack.com&hl=en&gl=US&ceid=US:en",
}

def _http_feed(url, _hops=0):
    """Fetch feed bytes with full browser headers, then hand to feedparser.
    Returns (feed, http_status). feedparser's own fetcher trips Cloudflare more
    often than a plain request with browser-like headers does.
    Follows redirects manually — pre-3.11 urllib doesn't auto-follow HTTP 308."""
    req = urllib.request.Request(url, headers={
        "User-Agent": _FEED_UA,
        "Accept": "application/rss+xml,application/xml,text/xml,*/*",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return feedparser.parse(r.read()), r.status
    except urllib.error.HTTPError as he:
        loc = he.headers.get("Location") if he.headers else None
        if he.code in (301, 302, 303, 307, 308) and loc and _hops < 3:
            return _http_feed(urllib.parse.urljoin(url, loc), _hops + 1)
        raise

def _fetch(sources):
    arts = []
    for name, url in sources:
        try:
            try:
                feed, status = _http_feed(url)
            except urllib.error.HTTPError as he:
                feed, status = None, he.code
            except Exception:
                feed, status = None, "?"
            if not feed or not feed.entries:
                # transport failed or empty — feedparser's own fetcher handles some
                # redirect/encoding cases urllib doesn't (e.g. 308 on older Pythons)
                fp = feedparser.parse(url, agent=_FEED_UA,
                    request_headers={"Accept":"application/rss+xml,application/xml,text/xml,*/*"})
                if fp.entries:
                    feed = fp
                else:
                    print(f"  ⚠  {name}: 0 entries (HTTP {status})", end="")
                    fb = FEED_FALLBACKS.get(name)
                    if fb:
                        try:
                            feed, _ = _http_feed(fb)
                            print(f" → Google News fallback: {len(feed.entries)} entries", end="")
                        except Exception:
                            feed = None
                    print()
            if not feed:
                continue
            for e in feed.entries[:MAX_PER_SOURCE]:
                arts.append({
                    "source":  name,
                    "title":   _clean_title(e.get("title","—")),
                    "link":    _resolve_gnews(e.get("link","#")),
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
    url = (f"https://news.google.com/rss/search?q=%22{q}%22+when:14d"
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
            return {"title": e.get("title", "—"), "link": _resolve_gnews(e.get("link", "#")),
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

LES_ECHOS_POLITIQUE_KW = [
    "politique","gouvernement","parlement","assemblée nationale","sénat",
    "macron","premier ministre","ministre","élection","parti","coalition",
    "réforme","loi","justice","syndicat","grève","manifestation","mobilisation",
    "idées","débat","essai","intellectuel","philosophie","démocratie",
    "immigration","laïcité","identité","liberté","droits","censure",
    "gauche","droite","extrême","rassemblement national","nfp","ps","lr",
    "opinion","éditorial","tribune","chronique","analyse","point de vue",
    "société","inégalités","fracture","populisme","souveraineté",
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
    "Stratechery":       2,
    "Scott Aaronson":    1,
    "Bits About Money":  1,
    "Reaction Wheel":    1,
    # ── Opinions. Les Echos' idees-debats feed is far more prolific than the
    # others and was taking 27 of the section's 40 cards, crowding the rest out.
    "Les Echos Idées":  12,
    "The Free Press":   15,
    "The Economist":    10,
    "Works in Progress": 6,
    "Lenny's Newsletter":1,
    "Pragmatic Engineer":1,
    "The NBS":           1,
    "SiliconMania":      1,
    "First Round Review":1,
    "Dezeen":            10,
    "Film Comment":       6,
    "Common Edge":        6,
    "Télérama Cinéma":   10,
    "Télérama Séries":   10,
    "The Ankler":        4,
    "Artnet":            10,
}
DEFAULT_CAP = 6  # all other sources

def _dedup_smart(arts):
    """Collapse near-duplicate articles (same topic / word overlap) keeping the most
    recent from each cluster. Used for the gossip section to reduce clutter."""
    STOP = {"the","a","an","in","of","to","and","for","on","at","is","are","was",
            "with","by","that","this","as","it","its","from","be","or","has","have",
            "had","will","than","after","but","not","about","new","de","la","le",
            "les","du","un","une","en","et","des","sur","pour","par","dans","est"}
    def words(t):
        return {w for w in re.sub(r"[^a-z0-9àâéèêëîïôùûü ]","",t.lower()).split()
                if w not in STOP and len(w)>2}
    used = set()
    result = []
    for i, a in enumerate(arts):
        if i in used: continue
        wi = words(a["title"])
        ei = _entities(a["title"])
        cluster = [a]
        for j, b in enumerate(arts):
            if j <= i or j in used: continue
            wj = words(b["title"])
            ej = _entities(b["title"])
            u  = wi | wj
            word_match  = bool(u) and len(wi & wj)/len(u) >= 0.40
            shared_ent  = ei & ej
            entity_match = (
                len(shared_ent) >= 2
                or any(len(e) >= 8 for e in shared_ent)
            )
            if word_match or entity_match:
                cluster.append(b); used.add(j)
        used.add(i)
        # Keep most recent from the cluster
        best = max(cluster, key=lambda x: x["ts"] or 0)
        result.append(best)
    return result

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
  --accent:#D42B17;--r:8px;--r-sm:5px;
  /* The panel colour that replaced Klein blue. It is light, so anything
     sitting on it needs --panel-ink rather than white. */
  --panel:#C6CEDE;--panel-ink:#16203A;
  /* gap from a section title to its first brick, used everywhere */
  --sec-gap:10px;
  --panel-ink-soft:rgba(22,32,58,.60);--panel-ink-faint:rgba(22,32,58,.34);
  --panel-line:rgba(22,32,58,.18);
  --serif:'Cormorant Garamond',Georgia,serif;
  --sans:'DM Sans',-apple-system,BlinkMacSystemFont,sans-serif;
  --display:-apple-system,BlinkMacSystemFont,'SF Pro Display','SF Pro Text',sans-serif;
}
@media(prefers-color-scheme:dark){
  :root{--bg:#060606;--bg2:#0d0d0d;--bg3:#131313;
    --border:#1c1c1c;--text:#d4d4d4;--muted:#555;--dim:#333;--accent:#E84040;
    --panel:#20263A;--panel-ink:#E7EAF3;
    --panel-ink-soft:rgba(231,234,243,.58);--panel-ink-faint:rgba(231,234,243,.32);
    --panel-line:rgba(231,234,243,.16)}
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
  font-size:9px;padding:7px 18px;border-radius:var(--r-sm);cursor:pointer;
  font-family:var(--sans);font-weight:700;letter-spacing:1.5px;
  text-transform:uppercase;transition:all .15s}
.btn:hover{background:var(--text);color:var(--bg);border-color:var(--text)}

/* ── Filter buttons (city tab bar) ──────────────────────────────── */
.fb{background:none;border:1px solid var(--border);color:var(--muted);
  font-size:8px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
  padding:4px 12px;border-radius:var(--r-sm);cursor:pointer;
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
/* left half: map on top, conflict names underneath */
.geo-left{flex:0 0 62%;min-width:0;display:flex;flex-direction:column;overflow:hidden}
#map{flex:1;min-height:0;overflow:hidden;border-radius:var(--r)}
/* conflict names — multi-column grid of chips under the map */
.cp-grid{
  flex:0 0 auto;max-height:44%;overflow-y:auto;
  display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
  gap:1px;padding:10px 12px 12px;
  scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.cp-grid::-webkit-scrollbar{width:2px}
.cp-grid::-webkit-scrollbar-thumb{background:var(--border)}
.cp-chip{
  display:flex;align-items:center;gap:9px;
  padding:10px 11px;min-width:0;
  background:none;border:none;border-radius:var(--r);
  font-family:inherit;font-size:15px;font-weight:400;color:var(--text);
  text-align:left;cursor:pointer;
  transition:background .15s ease,color .15s ease}
.cp-chip:hover{background:var(--bg3);color:var(--text)}
.cp-chip.has-new{color:var(--text);font-weight:500}
.cp-chip-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* right half: Politico feed, same row styling as the markets lists.
   #geo-feed overrides .story-list's 560px cap so the panel runs the full
   height of the section instead of stopping short. */
.cp{flex:1;display:flex;flex-direction:column;
  border-left:none;background:var(--bg2);overflow:hidden}
#geo-feed{flex:1;min-height:0;max-height:none;margin:16px 16px 16px 8px}
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
  background:#141414;color:#ddd;border:1px solid #252525;border-radius:var(--r)}
.dark-popup .leaflet-popup-tip{background:#141414}

/* ── Story list (Tech / Sports / Cities) ─────────────────────── */
.story-list{padding:10px;max-height:560px;overflow-y:auto;
  margin:0 16px 16px;border-radius:var(--r);background:var(--bg2);
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
  background:rgba(128,128,128,.15);border-radius:var(--r-sm);padding:2px 7px}
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
  margin:0 16px 16px;border-radius:var(--r);background:var(--bg2);
  display:flex;flex-direction:column;gap:5px;
  scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.paris-list::-webkit-scrollbar{width:2px}
.pi{display:flex;gap:12px;align-items:baseline;
  padding:12px 14px;border-bottom:none;background:var(--bg);border-radius:var(--r)}
.pi-src{font-size:7px;color:var(--muted);flex-shrink:0;
  white-space:nowrap;letter-spacing:.8px;text-transform:uppercase;font-weight:600;
  background:rgba(128,128,128,.15);border-radius:var(--r-sm);padding:2px 6px}
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
.cal-erow.ev-live{background:var(--bg3);border-radius:var(--r-sm);
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
.cal-erow.ev-sel{background:var(--bg3);border-radius:var(--r-sm);
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
  padding:0 60px;border-top:3px solid var(--panel);background:var(--panel)}
.hero-eyebrow{font-size:8px;letter-spacing:3.5px;text-transform:uppercase;
  color:rgba(255,255,255,.55);font-family:var(--sans);margin-bottom:16px;display:block}
.hero-h1{font-family:var(--display);font-size:clamp(60px,8.5vw,118px);
  font-style:normal;font-weight:700;color:var(--panel-ink);
  letter-spacing:-3px;line-height:.93;margin-bottom:36px}
.hero-meta{display:flex;align-items:center;gap:16px;margin-bottom:36px}
.hero-count{font-size:9px;color:var(--panel-ink);letter-spacing:.9px;
  font-weight:600;text-transform:uppercase}
.hero-date-str{font-size:9px;color:var(--panel-ink-soft);letter-spacing:.9px;text-transform:uppercase}
.hero-sep{color:var(--panel-ink-faint);font-size:12px}
.hero-hint{position:absolute;bottom:90px;left:60px;font-size:7.5px;
  letter-spacing:2.5px;text-transform:uppercase;color:var(--panel-ink-faint)}
.hero-sec .ticker{position:absolute;bottom:0;left:0;right:0;
  background:rgba(0,0,0,.05);border-bottom:none;border-top:1px solid var(--panel-line)}
.hero-sec .t-item{color:var(--panel-ink-soft);border-right-color:var(--panel-line)}
.hero-sec .t-item:hover{color:var(--panel-ink)}

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
.snap-geo .cp{background:var(--bg);border-left:none;position:relative;z-index:1}
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

.snap-feed{display:flex;flex-direction:column;overflow:hidden;background:var(--panel)}
.snap-feed>.two-col{flex:1;min-height:0;border-bottom:none}
.snap-feed .two-col>.section{height:100%;display:flex;flex-direction:column;
  overflow:hidden;border-bottom:none;background:var(--panel)}
.snap-feed .sec-hd{background:var(--panel)!important}
.snap-feed .sec-hd-text{color:var(--panel-ink)!important}
.snap-feed .poly-band{background:var(--panel)!important}
/* ── Match inner gap of article columns to bottom panel gap (8px each side = 16px total) ── */
.snap-feed .two-col>.section:first-child .story-list{margin-right:8px}
.snap-feed .two-col>.section:last-child .story-list{margin-left:8px}
/* ── Zero bottom margin on story-list so gap to bottom panels = only track's 16px top ── */
.snap-feed .two-col>.section .story-list{margin-bottom:0}
/* ── Compact band label (replaces sec-hd in poly/price bands) ── */
.band-label{font-size:9px;font-weight:700;letter-spacing:1px;
  text-transform:uppercase;color:var(--muted);
  padding:5px 16px 0;flex-shrink:0}
/* ── Markets price band ──────────────────────────────────────── */
/* ── snap-feed bottom row: Polymarket (left) + Markets (right) ── */
.snap-feed-bottom{flex:1 0 0;min-height:0;display:flex;flex-direction:row;
  background:var(--panel);overflow:hidden}
.snap-feed-bottom .poly-band{flex:1;min-width:0}
.snap-feed-bottom .price-band{flex:1;min-width:0}
.price-band{display:flex;flex-direction:column;
  border-top:none;background:var(--panel);overflow:hidden}
.price-band .sec-hd{display:none!important}
.price-band-track{flex:1;overflow-x:auto;overflow-y:hidden;
  margin:16px 16px 16px 8px;border-radius:var(--r);background:var(--bg2);
  display:flex;flex-direction:row;align-items:stretch;
  gap:6px;padding:6px;scrollbar-width:none}
.price-band-track::-webkit-scrollbar{display:none}
.price-tile{flex:0 0 auto;min-width:90px;display:flex;flex-direction:column;
  justify-content:center;gap:3px;padding:6px 12px;
  border-radius:var(--r);background:var(--bg);cursor:default;
  transition:background .2s ease}
.price-tile:hover{background:#3a3a3c}
.price-tile-name{font-size:7.5px;font-weight:700;letter-spacing:1px;
  text-transform:uppercase;color:var(--muted)}
.price-tile-val{font-size:13px;font-weight:600;color:var(--text);font-variant-numeric:tabular-nums}
.price-tile-chg{font-size:9px;font-weight:600;font-variant-numeric:tabular-nums}
.price-tile-chg.up{color:#16A34A}
.price-tile-chg.dn{color:#DC2626}
.price-tile-loading{font-size:10px;color:var(--muted);padding:0 12px;align-self:center}
/* ── Polymarket band ─────────────────────────────────────────── */
.poly-band{display:flex;flex-direction:column;
  border-top:none;background:var(--panel);overflow:hidden}
.poly-band .sec-hd{display:none!important}
.poly-band-label{display:none}
.poly-band-track{flex:1;overflow:hidden;position:relative;
  margin:16px 8px 16px 16px;border-radius:var(--r);background:var(--bg2);padding:8px 0}
.poly-band-items{display:flex;height:100%;width:max-content;gap:8px;padding:0 8px;
  animation:ticker-scroll 60s linear infinite}
.poly-band:hover .poly-band-items{animation-play-state:paused}
/* card */
.poly-card{flex:0 0 220px;height:100%;display:flex;flex-direction:column;
  justify-content:center;gap:6px;
  padding:14px 16px;border-right:none;border-radius:var(--r);
  background:var(--bg);
  text-decoration:none;transition:transform .2s ease;cursor:pointer}
.poly-card:hover{transform:scale(1.04);z-index:2;position:relative;background:#3a3a3c}
.poly-card:hover .poly-card-q{color:#fff}
.poly-card:hover .poly-out-name{color:rgba(255,255,255,.6)}
.poly-card-q{font-size:12px;font-weight:600;color:var(--text);
  line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden}
.poly-card-outcomes{display:flex;flex-direction:column;gap:3px}
.poly-outcome{display:flex;align-items:center;gap:8px}
.poly-out-name{font-size:11px;color:var(--muted);
  flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.poly-out-pct{font-size:10.5px;font-weight:700;
  padding:2px 7px;border-radius:var(--r-sm);flex-shrink:0}
.poly-out-pct.high{color:#16A34A;background:rgba(22,163,74,.12)}
.poly-out-pct.low{color:#DC2626;background:rgba(220,38,38,.12)}
.poly-card-vol{font-size:9px;color:var(--dim);letter-spacing:.3px;margin-top:2px}
.snap-feed .story-list{flex:1;max-height:none;overflow-y:auto;
  padding:10px;margin:0 16px 16px;border-radius:var(--r);background:var(--bg2);
  display:flex;flex-direction:column;gap:5px}
/* story rows — white card on grey container */
.snap-feed .sg{
  border-bottom:none;padding:12px 14px;margin:0;
  background:var(--bg);border-radius:var(--r);
  position:relative;overflow:visible}
.snap-feed .sg::before{
  content:'';position:absolute;inset:0;
  background:#3a3a3c;border-radius:var(--r);
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
.snap-feed .sg-multi.open .sg-title{color:#fff!important;opacity:1}
.snap-feed .sg:hover .sg-time,
.snap-feed .sg:hover .sg-cnt,
.snap-feed .sg-multi.open .sg-time,.snap-feed .sg-multi.open .sg-cnt{color:rgba(255,255,255,.5)}
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
  gap:12px;padding:var(--sec-gap) 12px 8px;
  overflow-x:auto;overflow-y:hidden;
  border-top:none!important;
  scrollbar-width:none}
.snap-culture .cards::-webkit-scrollbar{display:none}
.snap-culture .card,.mkt-slow .card{
  display:block!important;
  position:relative;
  width:100%;height:100%;
  border-right:none;
  border-radius:var(--r);overflow:hidden;
  cursor:pointer;
  transition:transform .2s ease,box-shadow .2s ease,z-index .2s ease;
  text-decoration:none}
.snap-culture .card:hover:not(.cv-open),.mkt-slow .card:hover{
  transform:scale(1.05);
  z-index:2;
  box-shadow:0 10px 30px rgba(0,0,0,.45)}
/* image fills the whole card */
.snap-culture .ci,.mkt-slow .ci{
  position:absolute!important;inset:0!important;
  height:100%!important;width:100%!important;flex-shrink:0}
.snap-culture .ci::after,.mkt-slow .ci::after{
  background:linear-gradient(to top,rgba(0,0,0,.45) 0%,rgba(0,0,0,.1) 40%,transparent 70%)}
/* source label — dark pill so it reads on any image */
.snap-culture .cs,.mkt-slow .cs{
  top:9px;bottom:auto;z-index:3;
  background:rgba(0,0,0,.62);
  backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px);
  border-radius:var(--r-sm);padding:3px 7px;
  color:#fff!important;font-size:8px;letter-spacing:1.2px}
/* normal text bottom */
.snap-culture .cb,.mkt-slow .cb{
  position:absolute!important;bottom:0;left:0;right:0;
  padding:22px 11px 12px;background:none;
  display:flex;flex-direction:column;justify-content:flex-end;z-index:2;
  transition:opacity .2s}
.snap-culture .card.cv-open .cb{opacity:0;pointer-events:none}
.snap-culture .ct,.mkt-slow .ct{
  color:#fff!important;opacity:1!important;
  font-size:18px;margin-bottom:4px;line-height:1.3;font-weight:500;
  text-shadow:0 1px 6px rgba(0,0,0,1),0 2px 10px rgba(0,0,0,.8)}
.snap-culture .ctime,.mkt-slow .ctime{color:rgba(255,255,255,.45);font-size:11px}
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
  border-radius:var(--r-sm);padding:3px 9px;text-decoration:none;
  transition:background .15s}
.snap-culture .cv-read:hover{background:rgba(255,255,255,.25)}
/* ── culture event band (bottom 1/4 of culture section) ──── */
.snap-culture .culture-cal-band{
  flex:1 0 0;min-height:0;
  /* centre, don't stretch — stretching sets the height independently of the
     width, which is what turned the event bubbles into ovals */
  display:flex;flex-direction:row;align-items:center;
  overflow-x:auto;overflow-y:hidden;
  gap:8px;
  padding:8px 12px 10px;
  border-top:none;
  -webkit-overflow-scrolling:touch;scrollbar-width:none}
.snap-culture .culture-cal-band::-webkit-scrollbar{display:none}
/* Size from the height so width follows it — guarantees a true circle
   whatever the band's height happens to be. */
.snap-culture .culture-cal-band .cal-ev-card{
  flex:0 0 auto;
  height:100%;width:auto;aspect-ratio:1/1;
  position:relative;border-radius:50%;overflow:hidden;
  cursor:pointer;opacity:.75;
  transition:opacity .3s ease,box-shadow .25s ease}
.snap-culture .culture-cal-band .cal-ev-card.ev-center{opacity:1}
.snap-culture .culture-cal-band .cal-ev-card.ev-past{
  opacity:.4;cursor:default;filter:grayscale(.6)}
.snap-culture .culture-cal-band .cal-ev-card.ev-past.ev-center{opacity:.55;filter:grayscale(.4)}
.snap-culture .culture-cal-band .cal-ev-card.ev-live{
  opacity:1;box-shadow:0 4px 20px rgba(0,0,0,.35)}
/* overlay backdrop */
.ccv-backdrop{
  position:fixed;inset:0;background:rgba(0,0,0,.6);
  z-index:9998;display:none;backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px)}
.ccv-backdrop.active{display:block}
/* portal overlay — card is moved to document.body so all styles are self-contained */
.cal-ev-portal-open{
  position:fixed!important;
  left:50%!important;top:50%!important;
  transform:translate(-50%,-50%)!important;
  width:min(65vw,860px)!important;
  height:min(72vh,680px)!important;
  z-index:9999!important;
  border-radius:var(--r)!important;overflow:hidden!important;
  box-shadow:0 32px 100px rgba(0,0,0,.85)!important;
  opacity:1!important;cursor:default;display:block!important}
.cal-ev-portal-open .cal-ev-bg{
  position:absolute;inset:0;
  background-size:cover!important;background-position:center top!important}
.cal-ev-portal-open .cal-ev-body{
  position:absolute;bottom:0;left:0;right:0;
  padding:40px 20px 16px;
  background:linear-gradient(to top,rgba(0,0,0,.8) 0%,transparent 100%);
  transform:none}
.cal-ev-portal-open .cal-ev-meta{display:flex;align-items:center;gap:6px;margin-bottom:6px}
.cal-ev-portal-open .cal-ev-cat-chip{
  font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
  color:rgba(255,255,255,.8);background:rgba(255,255,255,.15);
  border:1px solid rgba(255,255,255,.3);border-radius:var(--r-sm);padding:3px 8px}
.cal-ev-portal-open .cal-live-badge{
  font-size:9px;font-weight:700;color:#fff;background:#16A34A;
  border-radius:var(--r-sm);padding:3px 7px}
.cal-ev-portal-open .cal-ev-name{
  font-size:clamp(18px,2.2vw,32px);font-weight:700;
  color:#fff;line-height:1.15;margin-bottom:5px;
  text-shadow:0 2px 8px rgba(0,0,0,.6)}
.cal-ev-portal-open .cal-ev-range{
  font-size:13px;color:rgba(255,255,255,.6);font-weight:300}
.cal-ev-portal-open .cal-ev-panel{
  position:absolute;left:0;right:0;bottom:0;height:52%;
  background:rgba(248,248,248,.98);
  padding:18px 22px 20px;
  overflow-y:auto;scrollbar-width:thin;
  transform:translateY(0)!important;transition:none}
.cal-ev-portal-open .cal-ev-panel-hd{
  font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
  color:#888;margin-bottom:10px}
.cal-ev-portal-open .cal-det-art{
  display:flex;flex-direction:column;gap:2px;
  padding:9px 0;border-bottom:1px solid #e5e5e5;text-decoration:none}
.cal-ev-portal-open .cal-det-art:last-of-type{border-bottom:none}
.cal-ev-portal-open .cal-det-art-title{font-size:13px;color:#111;line-height:1.4;font-weight:400}
.cal-ev-portal-open .cal-det-art-meta{font-size:10px;color:#888;margin-top:2px}
.cal-ev-portal-open .cal-det-none{font-size:11px;color:#888;margin:4px 0}
.cal-ev-portal-open .cal-search-link{
  display:inline-block;margin-top:12px;font-size:10px;font-weight:600;
  color:#555;text-decoration:none;border-bottom:1px solid #ccc}
.cal-ev-portal-open .cal-search-link:hover{color:#111}
.snap-culture .culture-cal-band .cal-ev-bg{
  position:absolute;inset:0;
  background:linear-gradient(150deg,#FF8A3D 0%,#E8590C 55%,#C74405 100%)!important}
.snap-culture .culture-cal-band .cal-ev-card.ev-past .cal-ev-bg{filter:grayscale(.4);opacity:.6}
.snap-culture .culture-cal-band .cal-ev-body{
  position:absolute;top:50%;left:10%;right:10%;
  transform:translateY(-50%);
  padding:0;
  background:none;
  transition:none}
.snap-culture .culture-cal-band .cal-ev-card.ev-open .cal-ev-body{transform:translateY(-6px)}
.snap-culture .culture-cal-band .cal-ev-meta{display:flex;align-items:center;gap:6px;margin-bottom:4px}
.snap-culture .culture-cal-band .cal-ev-cat-chip{
  font-size:8px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
  color:rgba(255,255,255,.75);background:rgba(255,255,255,.15);
  border:1px solid rgba(255,255,255,.25);border-radius:var(--r-sm);padding:2px 7px}
.snap-culture .culture-cal-band .cal-live-badge{
  font-size:8px;font-weight:700;letter-spacing:.5px;
  color:#fff;background:#16A34A;border-radius:var(--r-sm);padding:2px 6px;
  animation:live-pulse 2s ease-in-out infinite}
.snap-culture .culture-cal-band .cal-ev-name{
  font-size:clamp(11px,1.2vw,17px);font-weight:600;
  color:#fff;line-height:1.2;margin-bottom:3px;
  text-shadow:0 1px 6px rgba(0,0,0,.7)}
.snap-culture .culture-cal-band .cal-ev-range{
  font-size:10px;color:rgba(255,255,255,.6);font-weight:300}
.snap-culture .culture-cal-band .cal-ev-panel{
  position:absolute;left:0;right:0;bottom:0;height:55%;
  background:rgba(242,242,247,.97);
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  padding:16px 20px 18px;
  overflow-y:auto;scrollbar-width:thin;
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

.snap-bottom{background:var(--panel)}
.snap-bottom .fb{background:rgba(255,255,255,.35);border-color:var(--panel-line);color:var(--panel-ink)}
.snap-bottom .fb.on{background:var(--panel-ink);color:var(--panel);border-color:var(--panel-ink)}
.snap-bottom .fb:hover:not(.on){background:rgba(255,255,255,.6);border-color:var(--panel-ink-soft);color:var(--panel-ink)}
.snap-bottom>.three-col{height:100%;border-bottom:none}
.snap-bottom .three-col>.section{height:100%!important;background:var(--panel)}
.snap-bottom .sec-hd{background:var(--panel)!important}
.snap-bottom .sec-hd-text{color:var(--panel-ink)!important}
.snap-bottom .story-list,.snap-bottom .paris-list{max-height:none}

/* ── shared card token (used below) ─────────────────────────
   padding: 11px 12px  |  gap: 5px  |  radius: var(--r)
   ────────────────────────────────────────────────────────── */

/* ── snap-bottom: sport / cities / paris ─────────────────── */
.snap-bottom .story-list,.snap-bottom .paris-list{
  padding:10px;margin:0 16px 16px;border-radius:var(--r);background:var(--bg2);
  display:flex;flex-direction:column;gap:5px}
/* inner gaps: 8px each side where columns face each other = 16px total gap */
.snap-bottom .three-col>.section:nth-child(1) .story-list,
.snap-bottom .three-col>.section:nth-child(1) .paris-list{margin-right:8px}
.snap-bottom .three-col>.section:nth-child(2) .story-list,
.snap-bottom .three-col>.section:nth-child(2) .paris-list{margin-left:8px;margin-right:8px}
.snap-bottom .three-col>.section:nth-child(3) .story-list,
.snap-bottom .three-col>.section:nth-child(3) .paris-list{margin-left:8px}
.snap-bottom .sg,.snap-bottom .pi{
  border-bottom:none;padding:12px 14px;margin:0;
  background:var(--bg);border-radius:var(--r);
  position:relative;overflow:visible}
.snap-bottom .sg::before,.snap-bottom .pi::before{
  content:'';position:absolute;inset:0;
  background:#3a3a3c;border-radius:var(--r);
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
.snap-bottom .pi:hover .pi-title{color:#fff!important;opacity:1}
.snap-bottom .sg:hover .sg-time,
.snap-bottom .sg:hover .sg-cnt,
.snap-bottom .pi:hover .pi-t{color:rgba(255,255,255,.5)}
.snap-bottom .sg-arts{border-top:1px solid rgba(0,0,0,.1);margin-top:8px;padding-top:0}
.snap-bottom .sg-art-link{border-bottom:1px solid rgba(0,0,0,.07)}

/* ── snap-gossip ─────────────────────────────────────────── */
.snap-gossip{background:var(--bg)}
.snap-gossip>.gos-section{height:100%;display:flex;flex-direction:column;background:var(--bg)}
.snap-gossip .sec-hd{background:var(--bg)!important}
.gos-grid{
  flex:1;min-height:0;
  display:grid;
  grid-template-rows:repeat(4,1fr);
  grid-auto-flow:column;
  /* column width = row height → perfect squares */
  grid-auto-columns:calc((100vh - 66px - 3*10px - 56px) / 4);
  gap:10px;padding:var(--sec-gap) 16px 14px;
  overflow-x:auto;overflow-y:hidden;
  scrollbar-width:none}
.gos-grid::-webkit-scrollbar{display:none}
.gos-card{
  display:flex;flex-direction:column;justify-content:space-between;
  padding:12px;
  background:linear-gradient(135deg,#3a3a3c,#1c1c1e);
  border-radius:var(--r);
  text-decoration:none;
  overflow:hidden;
  position:relative;
  transition:transform .2s ease,box-shadow .2s ease}
.gos-card:hover{
  transform:scale(1.04);z-index:2;
  box-shadow:0 10px 30px rgba(0,0,0,.45)}
/* photo layer + scrim so the headline stays legible on any image */
.gos-img{
  position:absolute;inset:0;z-index:0;
  background-size:cover;background-position:center;
  transition:transform .35s ease}
.gos-img::after{
  content:'';position:absolute;inset:0;
  background:linear-gradient(to top,rgba(0,0,0,.88) 0%,rgba(0,0,0,.45) 45%,rgba(0,0,0,.12) 100%)}
.gos-card:hover .gos-img{transform:scale(1.06)}
/* no-image tile: deliberate colour block in the source's brand colour */
.gos-noimg{
  background:
    linear-gradient(150deg,rgba(255,255,255,.14) 0%,rgba(255,255,255,0) 42%),
    linear-gradient(to top,rgba(0,0,0,.42) 0%,rgba(0,0,0,0) 60%),
    var(--gos-col,#444)}
.gos-noimg::after{
  content:'';position:absolute;left:12px;right:12px;top:34px;height:1px;
  background:rgba(255,255,255,.22);z-index:1}
.gos-mark{
  position:absolute;right:4px;bottom:-30px;z-index:0;
  font-family:var(--serif);font-size:150px;line-height:1;
  color:rgba(255,255,255,.13);pointer-events:none;user-select:none}
.gos-noimg .gos-title{text-shadow:none}
.gos-noimg .gos-time{color:rgba(255,255,255,.62)}
/* keep text above photo/colour layers */
.gos-src,.gos-title,.gos-time{position:relative;z-index:2}
.gos-src{
  display:inline-block;align-self:flex-start;flex-shrink:0;
  font-size:7px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;
  color:#fff;padding:3px 8px;border-radius:var(--r-sm)}
.gos-title{
  flex:1;min-height:0;padding:8px 0 4px;
  font-size:clamp(14px,1.15vw,17px);font-weight:500;line-height:1.32;color:#fff;
  text-shadow:0 1px 6px rgba(0,0,0,.8);
  overflow:hidden;
  /* line-clamp where the engine honours it… */
  display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;
  /* …and a fade so that when it doesn't, long headlines dissolve at the
     bottom edge instead of being sliced through the middle of a word */
  -webkit-mask-image:linear-gradient(to bottom,#000 calc(100% - 15px),transparent 100%);
  mask-image:linear-gradient(to bottom,#000 calc(100% - 15px),transparent 100%)}
.gos-time{font-size:8px;color:rgba(255,255,255,.55);letter-spacing:.3px;flex-shrink:0}

/* ── snap-geo: conflict accordion items ──────────────────── */
.snap-geo .cp-list{padding:10px;margin:0 10px 10px;border-radius:var(--r);background:var(--bg2);
  display:flex;flex-direction:column;gap:5px;overflow-y:auto;
  position:relative;z-index:1;pointer-events:auto}
.snap-geo .cp-item{
  border-bottom:none;
  background:var(--bg);border-radius:var(--r);overflow:hidden;
  flex-shrink:0}
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
  position:relative;border-radius:var(--r);overflow:hidden;
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
  border:1px solid rgba(255,255,255,.18);border-radius:var(--r-sm);padding:3px 9px}
.snap-cal .cal-live-badge{
  font-size:9px;font-weight:700;letter-spacing:.6px;
  color:#fff;background:#16A34A;
  border-radius:var(--r-sm);padding:3px 8px;
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
  .snap-sec{height:auto;overflow:visible;clip-path:none}

  /* hero */
  .hero-sec{min-height:100svh;padding:calc(48px + env(safe-area-inset-top,0px)) 20px 72px}
  .hero-h1{font-size:clamp(42px,13vw,68px);letter-spacing:-2px;margin-bottom:20px}
  .hero-meta{margin-bottom:20px}
  .hero-hint{display:none}
  .hero-sec .ticker{position:relative;bottom:auto;margin-top:24px}

  /* geo map — kill the !important locks so height:auto works */
  .snap-geo{height:auto!important;overflow:visible!important}
  .snap-geo>.section{height:auto!important;display:block!important}
  .snap-geo .map-wrap{flex-direction:column;height:auto!important;min-height:0!important}
  .snap-geo .geo-left{flex:none;width:100%;overflow:visible}
  .snap-geo #map{height:52vw!important;min-height:220px;max-height:320px;width:100%!important;
    flex:none}
  /* conflict names: 2 columns on a phone, no inner scroll */
  .cp-grid{grid-template-columns:repeat(2,minmax(0,1fr));
    max-height:none;overflow:visible;padding:10px 12px}
  .cp-chip{font-size:12px;padding:9px 8px}
  .snap-geo .cp{height:auto;width:100%}

  /* feed: reset flex column so sections just stack */
  .snap-feed{display:block}
  .snap-feed>.two-col{flex:none;height:auto;display:block}
  .snap-feed .two-col>.section{height:auto}
  /* story lists flow with the page — no nested scrollbars */
  .snap-feed .story-list{max-height:none;overflow:visible}
  /* long lists collapse to 8 items until "show more" (JS adds .mb-clamp) */
  .mb-clamp>.sg:nth-child(n+9),.mb-clamp>.pi:nth-child(n+9){display:none}

  /* polymarket + markets: stack full-width instead of half columns */
  .snap-feed-bottom{flex:none;flex-direction:column;height:auto}
  .snap-feed-bottom .poly-band{flex:none;width:100%;height:170px;min-height:0}
  .snap-feed-bottom .price-band{flex:none;width:100%;height:140px;min-height:0}
  .poly-band-track{margin:14px 16px 6px}
  .price-band-track{margin:6px 16px 14px}
  .poly-card{flex:0 0 210px}
  .poly-card-q{font-size:11px}
  .poly-out-name{font-size:10px}
  .poly-out-pct{font-size:9.5px}
  .poly-card-vol{font-size:8px}

  /* culture — display:flex must out-punch the desktop grid!important */
  .snap-culture>.section{height:auto}
  .snap-culture .culture-body{flex:none;display:block}
  .snap-culture .cards{
    flex:none;display:flex!important;flex-direction:row;
    grid-template-rows:unset;grid-auto-flow:unset;
    grid-auto-columns:unset;
    height:62vw;min-height:210px;max-height:280px;
    overflow-x:auto;overflow-y:hidden;
    padding:10px 16px 8px;gap:8px}
  .snap-culture .card{flex:0 0 65vw;width:auto;height:100%}
  /* event bubbles: uniform size, band grows to fit — nothing clipped */
  .snap-culture .culture-cal-band{
    height:auto;min-height:0;align-items:center;
    overflow-x:auto;overflow-y:visible;
    padding:12px 16px 16px;gap:10px}
  .snap-culture .culture-cal-band{height:46vw;max-height:190px}
  .snap-culture .culture-cal-band .cal-ev-name{font-size:11px}
  /* event popup fills the phone */
  .cal-ev-portal-open{width:calc(100vw - 28px)!important;height:min(76vh,560px)!important}

  /* bottom (sports / cities / paris) */
  .snap-bottom>.three-col{height:auto;display:block}
  .snap-bottom .three-col>.section{height:auto!important}
  .snap-bottom .story-list,.snap-bottom .paris-list{
    max-height:none;overflow:visible;margin-left:0!important;margin-right:0!important}

  /* gossip: keep clear of the tab bar */
  .snap-gossip{padding-bottom:calc(64px + env(safe-area-inset-bottom,0px))}

  /* cal */
  .snap-cal>.section{height:auto;overflow:visible}
  .snap-cal .cal-band{flex-wrap:nowrap;padding:12px 16px;overflow-x:auto}
  .snap-cal .cal-ev-name{font-size:clamp(18px,5vw,28px)}

}

/* ── Unread indicator dot ─────────────────────────────────── */
.unread-dot{
  display:inline-block;width:7px;height:7px;border-radius:50%;
  background:#22C55E;flex-shrink:0;vertical-align:middle;
  margin-right:6px;position:relative;top:-1px}
/* Card-style tiles: absolute top-right corner */
.card .unread-dot,.gos-card .unread-dot{
  position:absolute;top:10px;right:10px;margin:0;
  width:8px;height:8px;z-index:10;
  box-shadow:0 1px 5px rgba(0,0,0,.5)}

/* ── Culture tile with no image — designed colour block ─────────── */
.cul-noimg .ci{
  background:
    linear-gradient(150deg,rgba(255,255,255,.15) 0%,rgba(255,255,255,0) 45%),
    linear-gradient(to top,rgba(0,0,0,.5) 0%,rgba(0,0,0,0) 62%),
    var(--cul-col,#2A2A2E)!important}
.cul-noimg .ci::after{background:none!important}
.cul-noimg::before{
  content:'';position:absolute;left:11px;right:11px;top:32px;height:1px;
  background:rgba(255,255,255,.24);z-index:1;pointer-events:none}
.cul-noimg .ct{text-shadow:none!important}

/* ── Conflict modal (same behaviour as the culture event portal) ── */
.geo-backdrop{
  position:fixed;inset:0;background:rgba(0,0,0,.6);
  z-index:9998;display:none;
  backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px)}
.geo-backdrop.active{display:block}
.geo-modal{
  position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);
  width:min(62vw,760px);max-height:78vh;
  z-index:9999;display:none;flex-direction:column;
  background:var(--bg);border-radius:var(--r);overflow:hidden;
  box-shadow:0 32px 100px rgba(0,0,0,.85)}
.geo-modal.active{display:flex}
.geo-modal-hd{
  flex:0 0 auto;padding:20px 22px 16px;
  background:#D42B17;color:#fff}
.geo-modal-meta{display:flex;align-items:center;gap:8px;margin-bottom:7px}
.geo-modal-chip{
  font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
  color:rgba(255,255,255,.85);background:rgba(255,255,255,.16);
  border:1px solid rgba(255,255,255,.3);padding:3px 8px}
.geo-modal-name{font-size:clamp(19px,2vw,27px);font-weight:700;line-height:1.15}
.geo-modal-since{font-size:12px;color:rgba(255,255,255,.72);font-weight:300;margin-top:3px}
.geo-modal-body{
  flex:1;min-height:0;overflow-y:auto;padding:18px 22px 22px;
  scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.geo-modal-sum{font-size:13px;line-height:1.65;color:var(--text)}
.geo-modal-hd2{
  font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
  color:var(--muted);margin:18px 0 8px;
  padding-top:14px;border-top:1px solid var(--border)}
.geo-modal-art{
  display:block;padding:9px 0;border-bottom:1px solid var(--border);
  text-decoration:none}
.geo-modal-art:last-of-type{border-bottom:none}
.geo-modal-art-ttl{display:block;font-size:13px;color:var(--text);line-height:1.4}
.geo-modal-art-meta{display:block;font-size:10px;color:var(--muted);margin-top:2px}
.geo-modal-art:hover .geo-modal-art-ttl{color:var(--accent)}
.geo-modal-none{font-size:11px;color:var(--muted)}
.geo-modal-x{
  position:absolute;top:14px;right:16px;z-index:2;
  width:28px;height:28px;line-height:26px;text-align:center;
  background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.3);
  color:#fff;font-size:15px;cursor:pointer;padding:0}
.geo-modal-x:hover{background:rgba(255,255,255,.3)}

/* ── Mobile bottom tab bar (hidden on desktop) ───────────── */
.mb-nav{display:none;position:fixed;left:0;right:0;bottom:0;z-index:9000;
  background:rgba(252,252,252,.94);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border-top:1px solid rgba(0,0,0,.12);
  padding:7px 2px calc(7px + env(safe-area-inset-bottom,0px))}
.mb-nav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;
  color:rgba(0,0,0,.4);text-decoration:none;
  font-size:8px;font-weight:700;letter-spacing:1.1px;text-transform:uppercase;
  padding:3px 0;transition:color .18s}
.mb-nav a svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.6;
  stroke-linecap:round;stroke-linejoin:round}
.mb-nav a.on{color:#000}
@media(prefers-color-scheme:dark){
  .mb-nav{background:rgba(8,8,8,.92);border-top-color:rgba(255,255,255,.14)}
  .mb-nav a{color:rgba(255,255,255,.42)}
  .mb-nav a.on{color:#fff}
}
/* bar + show-more only exist on phones (must come after the display:none base) */
@media(max-width:768px){
  .mb-nav{display:flex}
  .mb-more{display:block}
  /* conflict modal goes near-fullscreen on a phone. Lives here rather than in
     the snap-override block above, which is declared before .geo-modal's base
     rule and would therefore lose on specificity ties. */
  .geo-modal{width:calc(100vw - 24px);max-height:82vh}
  .geo-modal-hd{padding:18px 18px 14px}
  .geo-modal-body{padding:16px 18px 20px}
}
/* "show more" button under clamped mobile lists (phone-only, see media below) */
.mb-more{display:none;width:100%;margin:6px 0 2px;padding:12px 0;
  background:none;border:1px dashed var(--border);border-radius:var(--r);
  color:var(--muted);font-family:inherit;font-size:9px;font-weight:700;
  letter-spacing:1.2px;text-transform:uppercase;cursor:pointer}
.mb-more:active{background:rgba(128,128,128,.12)}

/* ── Markets: three cadence bands of bricks ─────────────────────── */
.mkt-col{height:100%;display:flex;flex-direction:column;overflow:hidden;
  background:var(--panel)}
.mkt-body{flex:1;min-height:0;display:flex;flex-direction:column;
  gap:2px;padding:var(--sec-gap) 14px 14px;overflow:hidden}
.mkt-tier{display:flex;flex-direction:column;min-height:0}
.mkt-tier + .mkt-tier .mkt-tier-hd{padding-top:9px}
.mkt-tier-hd{
  display:flex;align-items:center;gap:9px;
  padding:0 2px 6px;
  font-size:7.5px;font-weight:700;letter-spacing:1.7px;text-transform:uppercase;
  color:var(--panel-ink-faint)}
.mkt-tier-hd:after{content:'';flex:1;height:1px;background:var(--panel-line)}
.mk-row{display:grid;gap:5px;min-height:0}

/* the brick */
.mk{
  display:flex;flex-direction:column;gap:4px;height:auto;
  padding:9px 10px;min-width:0;overflow:hidden;
  background:var(--bg);border-radius:var(--r);
  text-decoration:none;cursor:pointer;
  transition:transform .18s cubic-bezier(.2,.8,.2,1),box-shadow .18s ease}
.mk:hover{transform:translateY(-2px);box-shadow:0 6px 18px rgba(0,0,0,.14);z-index:2}
/* Flex children shrink by default, which squeezed every headline to zero
   height and left bricks 20px tall showing only the source and the time. */
.mk-hd{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.mk-hd .mk-time{flex:0 0 auto}
.mk-hd,.mk-t{flex:0 0 auto}
.mk-src{font-size:6.5px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;
  color:var(--muted)}
.mk-src-multi{color:var(--accent)}
.mk-t{font-size:11.5px;line-height:1.32;color:var(--text);
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.mk-time{font-size:7.5px;color:var(--muted)}
.mk-subs{display:none;margin-top:6px;border-top:1px solid var(--border);padding-top:5px}
.mk-multi.open .mk-subs{display:block}
.mk-multi.open .mk-t{-webkit-line-clamp:unset}
.mk-sub{display:block;padding:5px 0;text-decoration:none;
  border-bottom:1px solid var(--border)}
.mk-sub:last-child{border-bottom:none}
.mk-sub-src{display:block;font-size:6.5px;font-weight:700;letter-spacing:1px;
  text-transform:uppercase;color:var(--muted)}
.mk-sub-t{display:block;font-size:10.5px;line-height:1.35;color:var(--text)}

/* Today — the day's debriefs, a row of 2-3, dark so they read first */
.mkt-daily{flex:0 0 auto}
/* Fixed width, not 1fr: with 1fr a column holding one brick drew it double
   the width of a column holding two, so the two markets never matched. */
.mkt-daily .mk-row{
  /* same width as a Latest brick: the fast band is two columns with a 5px
     gap, so one Today brick spans exactly half of the row */
  grid-auto-flow:column;grid-auto-columns:calc((100% - 5px) / 2);
  padding-right:3px;
  overflow-x:auto;overflow-y:hidden;
  scroll-snap-type:x proximity;scrollbar-width:none}
.mkt-daily .mk-row::-webkit-scrollbar{display:none}
.mkt-daily .mk{scroll-snap-align:start}
.mkt-daily .mk{background:var(--panel-ink);min-height:78px}
.mkt-daily .mk-t{color:var(--panel);font-size:12.5px;font-weight:500;-webkit-line-clamp:3}
.mkt-daily .mk-src{color:var(--panel-ink-faint);color:rgba(255,255,255,.45)}
.mkt-daily .mk-time{color:rgba(255,255,255,.4)}

/* Latest — the fast wire, dense and scrollable */
.mkt-fast{flex:1;min-height:0}
.mkt-fast .mk-row{
  grid-template-columns:repeat(2,minmax(0,1fr));
  grid-auto-rows:min-content;
  align-content:start;overflow-y:auto;padding-right:3px;
  scrollbar-width:thin;scrollbar-color:var(--panel-line) transparent}
.mkt-fast .mk-row::-webkit-scrollbar{width:2px}
.mkt-fast .mk-row::-webkit-scrollbar-thumb{background:var(--panel-line)}

/* Slow reads — a third of the column. The card IS the Culture card: its look
   comes from the shared .snap-culture/.mkt-slow rules below, so the two
   sections cannot drift apart. Only layout lives here. */
.mkt-slow{flex:1 1 0;min-height:0}
.mkt-fast{flex:2 1 0;min-height:0}
.mkt-slow .mk-row{
  flex:1;grid-auto-flow:column;grid-auto-columns:168px;
  min-height:0;overflow-x:auto;overflow-y:hidden;
  scroll-snap-type:x proximity;scrollbar-width:none}
.mkt-slow .mk-row::-webkit-scrollbar{display:none}
.mkt-slow .card{
  position:relative;height:100%;width:100%;min-height:0;
  scroll-snap-align:start}

@media(max-width:768px){
  .mkt-body{padding:0 var(--m) var(--m);gap:0}
  .mkt-fast .mk-row{grid-template-columns:repeat(2,minmax(0,1fr));overflow:visible}
  .mkt-daily .mk-row{
    grid-auto-flow:column;grid-auto-columns:64vw;
    overflow-x:auto;scroll-snap-type:x proximity;padding-bottom:2px}
  .mkt-daily .mk{scroll-snap-align:start}
  .mkt-slow{flex:none}
  .mkt-slow .mk-row{grid-auto-columns:44vw;height:44vw}
  .mkt-fast{flex:none}
}


/* ── Phone layout pass ────────────────────────────────────────────
   Declared last on purpose: where it disagrees with the older mobile
   overrides above, this wins. */
@media(max-width:768px){
  /* One scale for the whole phone layout. Tighter than v1. */
  :root{--m:6px;--g:4px;--navh:56px}

  /* ── snap: each section seats itself on the screen ──────────────
     The section is both the snap target and its own scroller, so a swipe
     scrolls its content and only pages once that content runs out. */
  /* The document scrolls; sections just align to it. Proximity rather than
     mandatory so a section taller than the screen can still be read through
     instead of trapping the gesture. */
  html{scroll-snap-type:y proximity!important;scroll-behavior:smooth}
  .snap-sec{
    height:auto!important;min-height:100svh!important;
    scroll-snap-align:start;
    overflow:visible!important;
    padding-bottom:calc(var(--navh) + env(safe-area-inset-bottom,0px))}
  .hero-sec{padding-bottom:calc(var(--navh) + env(safe-area-inset-bottom,0px))}

  /* ── no black/white rule between sections ───────────────────────── */
  .sec-hd{border-top:none!important;border-bottom:none!important}
  .section,.gos-section{border-top:none!important}

  /* ── headers: title, then a shrinkable button row ───────────────── */
  .sec-hd{padding:0 var(--m);gap:8px;flex-wrap:nowrap;align-items:center}
  .sec-hd-text{font-size:15px;padding:11px 0 8px}
  .sec-hd > div{
    display:flex;gap:5px;flex:1 1 auto;min-width:0;justify-content:flex-end;
    overflow-x:auto;scrollbar-width:none}
  .sec-hd > div::-webkit-scrollbar{display:none}
  .fb{flex:0 0 auto;padding:5px 9px;font-size:7.5px;letter-spacing:.7px}

  /* ── 1. no map on a phone ───────────────────────────────────────── */
  .snap-geo #map{display:none!important}
  .snap-geo>.section{height:auto!important;display:block!important}
  .snap-geo .map-wrap{display:block;height:auto!important}
  .snap-geo .geo-left{flex:none;width:100%;overflow:visible}
  .cp-grid{
    grid-template-columns:repeat(2,minmax(0,1fr));
    max-height:none;overflow:visible;
    gap:var(--g);padding:var(--g) var(--m)}
  .cp-chip{font-size:13px;padding:11px 10px;
    background:var(--bg2);border-radius:var(--r)}

  /* ── 2. geo panel uses the Private Markets recipe ───────────────── */
  .snap-geo .cp{background:transparent;overflow:visible;height:auto;width:100%}

  /* ── 3. every list gets the same gutter, so the section colour
     frames all of them identically. The old rule pinned the bottom
     sections' lists to margin:0, which is why Sports / Cities / Paris
     had no blue edge while Markets did. */
  .story-list,.paris-list,#geo-feed,
  .snap-bottom .story-list,.snap-bottom .paris-list{
    margin:0 var(--m) var(--m)!important;
    padding:var(--g)!important;
    max-height:none;overflow:visible;
    gap:var(--g);background:var(--bg2)}
  .sg,.pi{padding:10px 11px}

  /* markets bands */
  .poly-band-track{margin:var(--g) var(--m) 2px}
  .price-band-track{margin:2px var(--m) var(--g)}

  /* ── 4. Culture: square bricks, three rows ──────────────────────── */
  /* Height chain must be unbroken: section -> .section -> body -> grid.
     A content-sized ancestor is why the grid ended up short and the rest of
     the screen was dead space. --sq is set by fitPhone() so three rows fill
     exactly and the bricks stay square. */
  .snap-culture{display:flex;flex-direction:column}
  .snap-culture>.section{flex:1;min-height:0;display:flex;flex-direction:column}
  .snap-culture .culture-body{flex:1;min-height:0;display:flex;flex-direction:column}
  .snap-culture .cards{
    --sq:44vw;
    flex:none;
    display:grid!important;
    grid-template-rows:repeat(3,var(--sq));
    grid-auto-flow:column;
    grid-auto-columns:var(--sq);
    height:auto;max-height:none;
    gap:var(--g);padding:var(--g) var(--m);
    overflow-x:auto;overflow-y:hidden;
    scroll-snap-type:x proximity;scroll-padding-left:var(--m)}
  .snap-culture .card{
    width:auto;height:auto;aspect-ratio:auto;
    scroll-snap-align:start}
  .snap-culture .ct{font-size:12px;line-height:1.25}
  .snap-culture .cb{padding:16px 9px 9px}

  /* ── 6. event circles: no clipped shadows, no dead gap ──────────── */
  .snap-culture .culture-cal-band{
    flex:0 0 auto;height:30vw;max-height:132px;
    align-items:center;
    gap:var(--g);padding:0 var(--m) var(--g);
    overflow-x:auto;overflow-y:hidden}
  /* the band clips on the x-axis, so a drop shadow gets sliced — drop it */
  .snap-culture .culture-cal-band .cal-ev-card,
  .snap-culture .culture-cal-band .cal-ev-card.ev-live{box-shadow:none!important}
  .snap-culture .culture-cal-band .cal-ev-name{font-size:10px}

  /* ── 5. Opinions: all rows on screen, swipe sideways ────────────── */
  .snap-gossip{display:flex;flex-direction:column}
  .snap-gossip>.gos-section{flex:1;min-height:0;display:flex;flex-direction:column}
  .gos-grid{
    flex:1;min-height:0;height:auto;
    display:grid!important;
    grid-template-rows:repeat(4,minmax(0,1fr));
    grid-auto-flow:column;
    grid-auto-columns:44vw;
    gap:var(--g);padding:var(--g) var(--m);
    overflow-x:auto;overflow-y:hidden;
    scroll-snap-type:x proximity;scroll-padding-left:var(--m)}
  .gos-card{scroll-snap-align:start;padding:9px}
  .gos-title{font-size:12.5px;-webkit-line-clamp:3}
  .gos-src{font-size:6.5px;padding:2px 6px}

  /* ── Paris — What's On fills its screen ─────────────────────────── */
  .snap-bottom>.three-col{display:block;height:auto}
  .snap-bottom .three-col>.section{height:auto!important}

  .mb-more{margin:3px 0 0}
}

/* ── Markets: the reference's flat editorial treatment ───────────
   Measured off oimachi.co rather than eyeballed from a screenshot: their
   insight items are transparent on the ground, not cards. Separation comes
   from whitespace and a tight type ramp, and the site never goes bolder
   than 500. Scoped to .snap-feed and declared last so it wins ties. */
.snap-feed{--panel:#F0F0F0;--panel-ink:#111;
  --mk-title:#000;--mk-meta:#696764;--mk-radius:5px}
.snap-feed .mkt-tier-hd{color:var(--mk-meta);letter-spacing:1.4px;font-weight:500}
.snap-feed .mkt-tier-hd:after{background:rgba(0,0,0,.10)}
/* flat: no fill, no shadow — whitespace does the separating */
.snap-feed .mk{
  background:transparent;
  border-radius:var(--mk-radius);
  padding:7px 8px;gap:6px;
  box-shadow:none;
  transition:background .18s ease}
.snap-feed .mk:hover{background:rgba(0,0,0,.045);transform:none;box-shadow:none}
/* meta: light weight, warm grey, one line — Source · 3h */
.snap-feed .mk-hd{justify-content:flex-start;gap:0;align-items:baseline}
.snap-feed .mk-src{font-size:11.5px;font-weight:300;letter-spacing:0;
  text-transform:none;color:var(--mk-meta)}
.snap-feed .mk-src-multi{color:var(--accent);font-weight:400}
.snap-feed .mk-time{font-size:11.5px;font-weight:300;letter-spacing:0;
  color:var(--mk-meta)}
.snap-feed .mk-time:before{content:"·";margin:0 5px;color:var(--mk-meta)}
/* headline: 500 and tight, never bold */
.snap-feed .mk-t{font-size:13.5px;font-weight:500;line-height:1.12;
  color:var(--mk-title);letter-spacing:0}
/* Today: a size step, still 500 */
.snap-feed .mkt-daily .mk{padding:8px}
.snap-feed .mkt-daily .mk-t{font-size:15.5px;font-weight:500;line-height:1.1;
  color:var(--mk-title)}
.snap-feed .mkt-daily .mk-src,
.snap-feed .mkt-daily .mk-time{color:var(--mk-meta)}
/* expanded multi-source list */
.snap-feed .mk-subs{border-top-color:rgba(0,0,0,.10)}
.snap-feed .mk-sub{border-bottom-color:rgba(0,0,0,.07)}
.snap-feed .mk-sub-src{font-size:10.5px;font-weight:300;letter-spacing:0;
  text-transform:none;color:var(--mk-meta)}
.snap-feed .mk-sub-t{font-size:12.5px;font-weight:400;line-height:1.2;
  color:var(--mk-title)}
/* filter buttons, same quiet register */
.snap-feed .fb{border-color:rgba(0,0,0,.18);color:var(--mk-meta);font-weight:500}
.snap-feed .fb.on{background:var(--mk-title);color:#F0F0F0;border-color:var(--mk-title)}
@media(prefers-color-scheme:dark){
  .snap-feed{--panel:#101010;--panel-ink:#EDEDED;
    --mk-title:#F4F4F4;--mk-meta:#8A8784}
  .snap-feed .mkt-tier-hd:after{background:rgba(255,255,255,.13)}
  .snap-feed .mk:hover{background:rgba(255,255,255,.06)}
  .snap-feed .mk-subs{border-top-color:rgba(255,255,255,.12)}
  .snap-feed .mk-sub{border-bottom-color:rgba(255,255,255,.09)}
  .snap-feed .fb{border-color:rgba(255,255,255,.2)}
  .snap-feed .fb.on{color:#101010}
}


/* Long reads: the reference's own card mechanics, read off its stylesheet —
   .posts_item-link:hover scales the image to 0.97 over 0.6s on
   cubic-bezier(.19,1,.22,1), with a .3rem radius and no shadow. So these
   shrink on hover rather than lifting, unlike the Culture bricks they
   otherwise share styling with. */
.mkt-slow .card{
  border-radius:.3rem;
  transition:transform .6s cubic-bezier(.19,1,.22,1)}
.mkt-slow .card:hover{
  transform:scale(.97);
  box-shadow:none;
  z-index:auto}

"""
# ══════════════════════════════════════════════════════════════════════════════
#  MOBILE NAV (bottom tab bar — rendered only ≤768px via CSS)
# ══════════════════════════════════════════════════════════════════════════════
PHONE_FIT_JS = """
<script>
(function(){
  /* Culture's bricks must be square AND fill the screen. CSS can express one
     or the other, not both: a column can't take its width from a row's flexed
     height. So measure once and hand the result back as --sq. */
  function fitPhone(){
    if (window.innerWidth > 768) return;
    var sec   = document.querySelector('.snap-culture');
    var cards = document.querySelector('.snap-culture .cards');
    var hd    = document.querySelector('.snap-culture .sec-hd');
    var band  = document.querySelector('.culture-cal-band');
    if (!sec || !cards || !hd) return;
    var cs   = getComputedStyle(sec);
    var pad  = parseFloat(cs.paddingBottom) || 0;
    var gap  = parseFloat(getComputedStyle(cards).rowGap) || 4;
    var cpad = parseFloat(getComputedStyle(cards).paddingTop) || 4;
    var avail = sec.getBoundingClientRect().height - pad
              - hd.getBoundingClientRect().height
              - (band ? band.getBoundingClientRect().height : 0)
              - cpad * 2;
    var sq = Math.floor((avail - gap * 2) / 3);
    if (sq > 40) cards.style.setProperty('--sq', sq + 'px');
  }
  fitPhone();
  window.addEventListener('resize', fitPhone);
  window.addEventListener('orientationchange', function(){ setTimeout(fitPhone, 250); });
  if (document.readyState !== 'complete') window.addEventListener('load', fitPhone);
})();
</script>
"""

MOBILE_NAV = """
<nav class="mb-nav" id="mb-nav" aria-label="Sections">
  <a href="#" data-sec=".snap-geo"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.9 5.7 3.9 9s-1.4 6.4-3.9 9c-2.5-2.6-3.9-5.7-3.9-9s1.4-6.4 3.9-9z"/></svg>World</a>
  <a href="#" data-sec=".snap-feed"><svg viewBox="0 0 24 24"><path d="M4 5h16M4 10h16M4 15h10M4 20h7"/></svg>News</a>
  <a href="#" data-sec=".snap-culture"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="15" rx="1"/><path d="M3 15l5-5 4 4 3-3 6 6"/><circle cx="9" cy="8.5" r="1.3"/></svg>Culture</a>
  <a href="#" data-sec=".snap-bottom"><svg viewBox="0 0 24 24"><path d="M6 3h12v3.5a6 6 0 0 1-12 0V3z"/><path d="M6 5H3.5c0 3 1.7 4.8 4.2 5.3M18 5h2.5c0 3-1.7 4.8-4.2 5.3M12 12.5V16m-4 5h8m-4-4.5V21"/></svg>Sports</a>
  <a href="#" data-sec=".snap-gossip"><svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-8 8H4l2.3-2.8A8 8 0 1 1 21 12z"/><path d="M8.5 11h.01M12 11h.01M15.5 11h.01"/></svg>Opinions</a>
</nav>
<script>
(function(){
  var mq = window.matchMedia('(max-width:768px)');
  var nav = document.getElementById('mb-nav');
  if (!nav) return;
  var links = [].slice.call(nav.querySelectorAll('a[data-sec]'));
  var secs = links.map(function(a){ return document.querySelector(a.getAttribute('data-sec')); });
  // tap -> smooth scroll to section
  links.forEach(function(a, i){
    a.addEventListener('click', function(e){
      e.preventDefault();
      if (secs[i]) secs[i].scrollIntoView({behavior:'smooth', block:'start'});
    });
  });
  // active tab follows scroll position
  function setOn(idx){ links.forEach(function(a, j){ a.classList.toggle('on', idx===j); }); }
  function onScroll(){
    if (!mq.matches) return;
    var y = window.scrollY + window.innerHeight * 0.35, cur = -1;
    secs.forEach(function(s, i){ if (s && s.offsetTop <= y) cur = i; });
    setOn(cur);
  }
  window.addEventListener('scroll', onScroll, {passive:true});
  onScroll();
  // collapse long lists to 8 items behind a "show more" button
  // (#city-list excluded: it has its own city filter)
  function clampLists(){
    if (!mq.matches) return;
    var lists = document.querySelectorAll(
      '.snap-feed .story-list, .snap-bottom .story-list:not(#city-list), .snap-bottom .paris-list');
    [].forEach.call(lists, function(list){
      if (list.dataset.mbClamped) return;
      var items = list.querySelectorAll(':scope > .sg, :scope > .pi');
      if (items.length <= 10) return;
      list.dataset.mbClamped = '1';
      list.classList.add('mb-clamp');
      var btn = document.createElement('button');
      btn.className = 'mb-more';
      btn.textContent = 'Show ' + (items.length - 8) + ' more ↓';
      btn.addEventListener('click', function(){
        list.classList.remove('mb-clamp');
        btn.remove();
      });
      list.appendChild(btn);
    });
  }
  clampLists();
})();
</script>
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
def build_geo_feed(arts):
    """Right-hand Politico panel — same row markup as the markets lists."""
    def _rank(a):
        try:
            return GEO_PINNED.index(a["source"])
        except ValueError:
            return len(GEO_PINNED)
    ordered = sorted(arts, key=_rank)
    rows = "".join(_build_group_row([a]) for a in ordered[:40])
    if not rows:
        rows = '<p style="font-size:11px;color:var(--dim);padding:14px 16px">No articles fetched.</p>'
    return f'<div class="story-list" id="geo-feed">{rows}</div>'

def build_map(conflicts_json, articles_json, geo_arts=()):
    return f"""
<div class="section">
  <div class="sec-hd">
    <span class="sec-hd-text">Geopolitics</span>
  </div>
  <div class="map-wrap">
    <div class="geo-left">
      <div id="map"></div>
      <div id="cp-grid" class="cp-grid"></div>
    </div>
    <div class="cp">
      {build_geo_feed(geo_arts)}
    </div>
  </div>
</div>
<div class="geo-backdrop" id="geo-backdrop"></div>
<div class="geo-modal" id="geo-modal" role="dialog" aria-modal="true">
  <button class="geo-modal-x" id="geo-modal-x" aria-label="Close">✕</button>
  <div class="geo-modal-hd">
    <div class="geo-modal-meta"><span class="geo-modal-chip" id="geo-modal-type"></span></div>
    <div class="geo-modal-name" id="geo-modal-name"></div>
    <div class="geo-modal-since" id="geo-modal-since"></div>
  </div>
  <div class="geo-modal-body" id="geo-modal-body"></div>
</div>
<script>
(function(){{
  var C = {conflicts_json};
  var A = {articles_json};
  var TC = {{ conflict:'#EF4444', tension:'#EF4444' }};
  var listEl = document.getElementById('cp-grid');
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
  /* centre/bounds exclude Antarctica — see _isPolar() below */
  var map = L.map('map',{{center:[26,10],zoom:2,minZoom:2,maxZoom:2,
    zoomControl:false,attributionControl:false,
    dragging:false,scrollWheelZoom:false,doubleClickZoom:false,
    touchZoom:false,keyboard:false,boxZoom:false,
    maxBounds:[[-58,-200],[85,200]],maxBoundsViscosity:1.0}});

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

    /* Skip Antarctica — any polygon lying entirely below 60°S. Keeps the map
       to the inhabited world so the dot grid isn't dragged down by an ice cap
       no conflict marker ever sits on. */
    function _isPolar(poly) {{
      var ring = poly[0] || [];
      for (var i = 0; i < ring.length; i++) {{
        if (ring[i][1] > -60) return false;
      }}
      return ring.length > 0;
    }}
    _landGeo.features.forEach(function(f) {{
      var g = f.geometry;
      if (g.type === 'Polygon') {{
        if (!_isPolar(g.coordinates)) drawRings(g.coordinates);
      }} else if (g.type === 'MultiPolygon') {{
        g.coordinates.forEach(function(poly) {{
          if (!_isPolar(poly)) drawRings(poly);
        }});
      }}
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
  /* ── Conflict modal — same open/close behaviour as the culture events ── */
  var mdl   = document.getElementById('geo-modal');
  var mdlBd = document.getElementById('geo-backdrop');
  function closeModal() {{
    mdl.classList.remove('active');
    mdlBd.classList.remove('active');
  }}
  mdlBd.addEventListener('click', closeModal);
  document.getElementById('geo-modal-x').addEventListener('click', closeModal);
  document.addEventListener('keydown', function(e){{
    if (e.key === 'Escape') closeModal();
  }});

  function toggleItem(id) {{
    var c = C.find(function(x){{return x.id===id;}});
    if (!c) return;
    var arts = A[c.id]||[];
    var artsHtml = arts.length
      ? '<div class="geo-modal-hd2">Recent Coverage</div>' + arts.map(function(a){{
          return '<a href="'+_esc(a.link)+'" target="_blank" rel="noopener" class="geo-modal-art">'
            +'<span class="geo-modal-art-ttl">'+_esc(a.title)+'</span>'
            +'<span class="geo-modal-art-meta">'+_esc(a.source)+(a.ago?' · '+a.ago:'')+'</span>'
            +'</a>';
        }}).join('')
      : '<div class="geo-modal-hd2">Recent Coverage</div>'
        +'<p class="geo-modal-none">No recent articles matched.</p>';
    document.getElementById('geo-modal-type').textContent  = c.type||'conflict';
    document.getElementById('geo-modal-name').textContent  = c.name;
    document.getElementById('geo-modal-since').textContent = 'Since '+(c.started||'—');
    document.getElementById('geo-modal-body').innerHTML =
      '<div class="geo-modal-sum">'+_esc(c.summary)+'</div>'+artsHtml;
    document.getElementById('geo-modal-body').scrollTop = 0;
    mdl.classList.add('active');
    mdlBd.classList.add('active');

    markSeen(id);
    _replacePulse(c);
    var chip = listEl.querySelector('[data-id="'+id+'"]');
    if (chip) chip.classList.remove('has-new');
  }}

  /* Build the conflict-name grid under the map */
  C.forEach(function(c){{
    var col    = TC[c.type]||'#888';
    var hasNew = isNew(c.id);

    var item = document.createElement('button');
    item.className='cp-chip'+(hasNew?' has-new':''); item.dataset.id=c.id;
    item.type='button';
    item.innerHTML=
       '<span class="dot" style="background:'+col+'"></span>'
      +'<span class="cp-chip-name">'+_esc(c.name)+'</span>';

    item.addEventListener('click',function(){{toggleItem(c.id);}});
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

def _fetch_evergreen(sources, n=1):
    """Latest n from each source, ignoring the recency window. For writers who
    publish a few times a year, where the newest piece is what matters, not
    whether it landed this week."""
    out = []
    for name, url in sources:
        try:
            arts = _fetch([(name, url)])
            arts.sort(key=lambda a: a["ts"] or 0, reverse=True)
            out.extend(arts[:n])
        except Exception as ex:
            print(f"  ⚠  {name}: {ex}")
    return out

def _fetch_art_newspaper(n=5):
    """Always return the latest n NYT Arts articles, ignoring age filter."""
    try:
        arts = _fetch([("The NYT Arts", ART_NEWSPAPER_FEED)])
        arts.sort(key=lambda a: a["ts"] or 0, reverse=True)
        return arts[:n]
    except Exception as ex:
        print(f"  ⚠  NYT Arts: {ex}")
        return []

_NSS_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# English articles only: /en/<section>/<numeric-id>/<slug>, excluding author/tag/search pages
_NSS_ARTICLE = re.compile(r'nssmag\.com/en/(?!author/|tag/|search/)[a-z0-9-]+/\d+/', re.IGNORECASE)
 
def _fetch_nss(n=5):
    """Latest n NSS articles from their monthly sitemap — real URLs, real dates, no Google News."""
    now = datetime.now()
    months = [(now.year, now.month)]
    months.append((now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12))
    arts, seen = [], set()
    for y, m in months:
        try:
            url = f"https://www.nssmag.com/sitemap.xml?year={y}&month={m}"
            req = urllib.request.Request(url, headers={"User-Agent": _NSS_UA})
            root = None
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=30) as r:
                        root = ET.fromstring(r.read())
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    import time as _t
                    _t.sleep(2)
            ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            for u in root.findall("s:url", ns):
                loc = (u.findtext("s:loc", default="", namespaces=ns) or "").strip()
                mod = (u.findtext("s:lastmod", default="", namespaces=ns) or "").strip()
                if not _NSS_ARTICLE.search(loc) or loc in seen:
                    continue
                seen.add(loc)
                try:
                    dt = datetime.fromisoformat(mod.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except Exception:
                    dt = None
                slug  = loc.rstrip("/").split("/")[-1].replace("-", " ")
                title = slug.title()
                arts.append({"source": "NSS Magazine", "title": title, "link": loc,
                             "date": dt, "ts": _ts(dt), "img": "", "snip": ""})
        except Exception as ex:
            print(f"  ⚠  NSS sitemap {y}-{m}: {ex}")
        if len(arts) >= n:
            break
    arts.sort(key=lambda a: a["ts"] or 0, reverse=True)
    print(f"    → {len(arts)} NSS articles (sitemap)")
    return arts[:n]

def _fetch_prices():
    """Fetch index/commodity/forex prices via Yahoo v8 at build time. Crypto via CoinGecko."""
    import urllib.parse, time as _time
    tickers = [
        ("^GSPC","S&P 500"), ("^IXIC","NASDAQ"), ("^DJI","Dow Jones"),
        ("GC=F","Gold"), ("CL=F","WTI Oil"), ("EURUSD=X","EUR/USD"),
        ("^VIX","VIX"), ("^TNX","10Y UST"),
    ]
    UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    out = []
    try:
        cg_url = ("https://api.coingecko.com/api/v3/simple/price"
                  "?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true")
        req = urllib.request.Request(cg_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            cg = json.loads(r.read())
        for cid, name, sym in [("bitcoin","Bitcoin","BTC-USD"),
                               ("ethereum","Ethereum","ETH-USD")]:
            if cid in cg:
                out.append({"name": name, "sym": sym,
                            "price": cg[cid].get("usd"),
                            "pct": cg[cid].get("usd_24h_change")})
    except Exception as ex:
        print(f"  ⚠  CoinGecko: {ex}")
    for sym, name in tickers:
        try:
            s = urllib.parse.quote(sym)
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{s}"
                   f"?interval=1d&range=2d")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            meta  = d["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            prev  = meta.get("previousClose") or meta.get("chartPreviousClose")
            pct   = ((price - prev) / prev * 100) if (price and prev) else None
            out.append({"name": name, "sym": sym, "price": price, "pct": pct})
        except Exception as ex:
            print(f"  ⚠  price {name}: {ex}")
            out.append({"name": name, "sym": sym, "price": None, "pct": None})
        _time.sleep(0.4)
    print(f"    → {sum(1 for p in out if p['price'] is not None)}/{len(out)} prices fetched")
    return out

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
        if markets:
            try:
                POLY_CACHE_FILE.write_text(json.dumps(markets), encoding="utf-8")
            except Exception:
                pass
        print(f"    → {len(markets)} Polymarket markets")
        return markets
    except Exception as ex:
        # French ISPs resolve gamma-api.polymarket.com to the ANJ regulator's
        # block page, so a build run from France always fails here while CI
        # (US runners) succeeds. Fall back to the last good payload rather than
        # publishing an empty band.
        print(f"  ⚠  Polymarket: {ex}")
        try:
            cached = json.loads(POLY_CACHE_FILE.read_text(encoding="utf-8"))
            if cached:
                print(f"    → reusing {len(cached)} cached markets")
                return cached
        except Exception:
            pass
        return []

def build_polymarket_band(markets):
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
    if markets:
        once  = "".join(card_html(m) for m in markets)
        items = once + once   # duplicate for seamless loop
        inner = f'<div class="poly-band-items">{items}</div>'
    else:
        inner = '<div style="padding:0 16px;font-size:11px;color:var(--muted);align-self:center">No data</div>'
    return (
        f'<div class="poly-band">'
        f'<div class="poly-band-track">'
        f'{inner}'
        f'</div></div>\n'
    )

def build_price_band(prices):
    def fmt(v, sym):
        if v is None: return "—"
        if sym == "EURUSD=X": return f"{v:.4f}"
        if sym == "^TNX":     return f"{v:.2f}%"
        if v >= 10000: return f"{v:,.0f}"
        if v >= 1000:  return f"{v:,.1f}"
        return f"{v:.2f}"
    tiles = ""
    for p in prices:
        chg = ""
        if p["pct"] is not None:
            cls  = "up" if p["pct"] >= 0 else "dn"
            sign = "+" if p["pct"] >= 0 else ""
            chg  = f'<div class="price-tile-chg {cls}">{sign}{p["pct"]:.2f}%</div>'
        tiles += (
            f'<div class="price-tile">'
            f'<div class="price-tile-name">{_s(p["name"])}</div>'
            f'<div class="price-tile-val">{fmt(p["price"], p["sym"])}</div>'
            f'{chg}</div>'
        )
    if not tiles:
        tiles = '<span class="price-tile-loading">No price data</span>'
    return (f'<div class="price-band">'
            f'<div class="price-band-track" id="price-band-track">{tiles}</div>'
            f'</div>\n')

def _sort_by_time(groups):
    """Sort groups purely by recency of their most recent article."""
    return sorted(groups, key=lambda g: max(a["ts"] or 0 for a in g), reverse=True)

def _one_per_source(groups):
    """The Today band has only three slots — spend them on three different
    debriefs rather than letting one source take two."""
    seen, out = set(), []
    for g in groups:
        src = g[0].get("_canon") or g[0]["source"]
        if src in seen:
            continue
        seen.add(src)
        out.append(g)
    return out

def _cadence(g):
    a = g[0]
    return (SOURCE_CADENCE.get(a["source"])
            or SOURCE_CADENCE.get(a.get("_canon") or "")
            or DEFAULT_CADENCE)

def _brick(g):
    """One article as a brick. Multi-source groups keep the click-to-expand
    behaviour the old rows had, just wearing a different shape."""
    primary  = g[0]
    n        = len(g)
    time_str = _ago(primary["date"])
    src      = _s(primary.get("_canon") or primary["source"])
    if n == 1:
        return (f'<a class="mk" href="{_s(primary["link"])}" target="_blank" rel="noopener">'
                f'<span class="mk-hd"><span class="mk-src">{src}</span>'
                f'<span class="mk-time">{time_str}</span></span>'
                f'<span class="mk-t">{_s(primary["title"])}</span></a>\n')
    arts = "".join(
        f'<a href="{_s(a["link"])}" target="_blank" rel="noopener" class="mk-sub" '
        f'onclick="event.stopPropagation()">'
        f'<span class="mk-sub-src">{_s(a.get("_canon") or a["source"])}</span>'
        f'<span class="mk-sub-t">{_s(a["title"])}</span></a>'
        for a in g)
    title = primary.get("_headline") or primary["title"]
    return (f'<div class="mk mk-multi" onclick="this.classList.toggle(\'open\')">'
            f'<span class="mk-hd"><span class="mk-src mk-src-multi">{n} sources</span>'
            f'<span class="mk-time">{time_str}</span></span>'
            f'<span class="mk-t">{_s(title)}</span>'
            f'<div class="mk-subs">{arts}</div></div>\n')

# Colour behind a slow-read card when no picture can be found
SLOW_SOURCE_COLORS = {
    "Not Boring":"#1E3A5F", "Silicon Carne":"#5C2118", "TBPN":"#123A2E",
    "Scott Aaronson":"#2A2A3E", "First Round Review":"#3B2A55",
    "Lenny's Newsletter":"#5A3A12", "Pragmatic Engineer":"#123A5C",
    "Bits About Money":"#2C4A3B", "Reaction Wheel":"#4A2A2A",
}

def _slow_card(g):
    """Slow reads use the Culture card: picture, source chip, title over a
    scrim — falling back to a colour tile when there's no image."""
    a   = g[0]
    src = a.get("_canon") or a["source"]
    img = a.get("img", "")
    col = SLOW_SOURCE_COLORS.get(a["source"], "#2A2A2E")
    if img:
        media = (f'<div class="ci" style="background-image:url({_s(img)});'
                 f'background-size:cover;background-position:center"></div>')
        cls = "card"
    else:
        media = f'<div class="ci" style="--cul-col:{col}"></div>'
        cls = "card cul-noimg"
    return (f'<a href="{_s(a["link"])}" target="_blank" rel="noopener" class="{cls}">'
            f'{media}'
            f'<span class="cs">{src}</span>'
            f'<div class="cb"><p class="ct">{_s(a["title"])}</p>'
            f'<span class="ctime">{_ago(a["date"])}</span></div></a>\n')

def _tier(label, groups, cls, limit):
    if not groups:
        return ""
    render = _slow_card if cls == "mkt-slow" else _brick
    bricks = "".join(render(g) for g in groups[:limit])
    return (f'<div class="mkt-tier {cls}">'
            f'<div class="mkt-tier-hd"><span>{label}</span></div>'
            f'<div class="mk-row">{bricks}</div></div>')

def _build_market_col(groups, label, hd_buttons="", body_id=""):
    """A markets column: today's debriefs on top, the fast wire in the middle,
    the slower essayists along the bottom."""
    buckets = {"daily": [], "fast": [], "slow": []}
    for g in _sort_by_time(groups):
        buckets[_cadence(g)].append(g)
    buckets["daily"] = _one_per_source(buckets["daily"])
    idattr = f' id="{body_id}"' if body_id else ""
    body = (
        _tier("Today", buckets["daily"], "mkt-daily", 8) +
        _tier("Latest", buckets["fast"], "mkt-fast", 60) +
        _tier("Slow reads", buckets["slow"], "mkt-slow", 14)
    )
    if not body:
        body = '<p style="font-size:11px;color:var(--panel-ink-soft);padding:14px 16px">No articles in the past 48h.</p>'
    return (f'<div class="section mkt-col">'
            f'<div class="sec-hd"><span class="sec-hd-text">{label}</span>{hd_buttons}</div>'
            f'<div class="mkt-body"{idattr}>{body}</div></div>\n')

def build_tech(groups):
    return _build_market_col(groups, "Private Markets")

def build_macro(groups):
    hd_buttons = (
        '<div style="display:flex;gap:6px">'
        '<button class="fb on" id="macro-all" onclick="filterMacro(\'all\')">All</button>'
        '<button class="fb" id="macro-finance" onclick="filterMacro(\'finance\')">Public Finance</button>'
        '<button class="fb" id="macro-crypto" onclick="filterMacro(\'crypto\')">Crypto</button>'
        '</div>'
    )
    # tag each brick with its category so the buttons can filter them
    buckets = {"daily": [], "fast": [], "slow": []}
    for g in _sort_by_time(groups):
        buckets[_cadence(g)].append(g)
    buckets["daily"] = _one_per_source(buckets["daily"])

    def tier(label, gs, cls, limit):
        if not gs: return ""
        out = ""
        for g in gs[:limit]:
            src = g[0].get("_canon") or g[0]["source"]
            cat = MACRO_CATEGORY.get(g[0]["source"]) or MACRO_CATEGORY.get(src, "finance")
            if cls == "mkt-slow":
                out += _slow_card(g).replace('class="card', f'class="macro-item card', 1) \
                                    .replace('rel="noopener"', f'rel="noopener" data-cat="{cat}"', 1)
                continue
            out += _brick(g).replace('class="mk"', f'class="mk macro-item" data-cat="{cat}"', 1) \
                            .replace('class="mk mk-multi"', f'class="mk mk-multi macro-item" data-cat="{cat}"', 1)
        return (f'<div class="mkt-tier {cls}">'
                f'<div class="mkt-tier-hd"><span>{label}</span></div>'
                f'<div class="mk-row">{out}</div></div>')

    body = (tier("Today", buckets["daily"], "mkt-daily", 8) +
            tier("Latest", buckets["fast"], "mkt-fast", 60) +
            tier("Slow reads", buckets["slow"], "mkt-slow", 14))
    if not body:
        body = '<p style="font-size:11px;color:var(--panel-ink-soft);padding:14px 16px">No articles in the past 48h.</p>'
    js = """<script>
function filterMacro(v){
  document.querySelectorAll('.fb[id^="macro-"]').forEach(function(b){
    b.classList.toggle('on', b.id==='macro-'+v||(v==='all'&&b.id==='macro-all'));
  });
  document.querySelectorAll('.macro-item').forEach(function(el){
    el.style.display=(v==='all'||el.dataset.cat===v)?'':'none';
  });
}
</script>"""
    return (f'<div class="section mkt-col">'
            f'<div class="sec-hd"><span class="sec-hd-text">Public Markets</span>{hd_buttons}</div>'
            f'<div class="mkt-body">{body}</div>{js}</div>\n')

def _build_cal_band_html(event_news={}):
    """Returns the HTML+JS for the compact event band embedded in the culture section."""
    today     = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    cat_col = {
        "culture":"#3a3a3c","fashion":"#EA580C","football":"#15803D",
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
            bg_style = "background:linear-gradient(135deg,#3a3a3c,#1c1c1e)"
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
  /* backdrop */
  var bd=document.createElement('div');
  bd.className='ccv-backdrop';
  document.body.appendChild(bd);
  /* portal: active card moved to body so it escapes band stacking context */
  var activeCard=null, placeholder=null;
  function closeAll(){{
    if(activeCard){{
      activeCard.classList.remove('cal-ev-portal-open');
      if(placeholder&&placeholder.parentNode){{
        placeholder.parentNode.insertBefore(activeCard,placeholder);
        placeholder.remove();
      }}
      activeCard=null;placeholder=null;
    }}
    bd.classList.remove('active');
    document.body.style.overflow='';
  }}
  bd.addEventListener('click',closeAll);
  document.addEventListener('keydown',function(e){{if(e.key==='Escape')closeAll();}});
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
      if(activeCard===card)return;
      closeAll();
      /* portal: move card to body so it escapes band stacking context */
      placeholder=document.createElement('div');
      placeholder.style.cssText='flex:0 0 12%;opacity:.4;pointer-events:none';
      card.parentNode.insertBefore(placeholder,card);
      document.body.appendChild(card);
      card.classList.add('cal-ev-portal-open');
      bd.classList.add('active');
      document.body.style.overflow='hidden';
      activeCard=card;
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
        col  = CULTURE_SOURCE_COLORS.get(a["source"], "#2A2A2E")
        if img:
            bg  = (f"background-image:url({_s(img)});"
                   f"background-size:cover;background-position:center;")
            cls = "card"
        else:
            # no image anywhere — a deliberate colour tile in the source's
            # colour, matching how the Opinions section handles the same case
            bg  = f"--cul-col:{col};"
            cls = "card cul-noimg"
        snip_html = f'<div class="cv-snip">{snip}</div>' if snip else ""
        html_cards += (
            f'<a href="{_s(a["link"])}" target="_blank" rel="noopener" class="{cls}">'
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
    return _sec("#D42B17","Culture", body)

def build_sports(arts):
    rows = "".join(_build_group_row([a]) for a in arts[:60])
    if not rows:
        rows = '<p style="font-size:11px;color:var(--dim)">No articles fetched.</p>'
    return _sec("#0C0C0C","Sports", f'<div class="story-list">{rows}</div>')

def build_cities(arts):
    rows = ""
    for a in arts[:60]:
        city = "marseille" if a["source"] in MARSEILLE_SOURCE_NAMES else "paris"
        rows += _build_group_row([a], extra_cls="city-item", data_attrs=f'data-city="{city}"')
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
        f'<span class="sec-hd-text">City Focus</span>'
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
def build_gossip(arts):
    tiles = ""
    for a in arts:
        col      = GOSSIP_SOURCE_COLORS.get(a["source"], "#444")
        time_str = _ago(a["date"])
        img      = a.get("img", "")
        if img:
            # photo card — image fills the tile, scrim keeps the headline readable
            media = (f'<span class="gos-img" '
                     f'style="background-image:url({_s(img)})"></span>')
            cls, style = "gos-card", ""
        else:
            # no image available (Google News / Cloudflare-blocked sources):
            # a deliberate colour tile in the source's brand colour
            media = '<span class="gos-mark" aria-hidden="true">"</span>'
            cls, style = "gos-card gos-noimg", f' style="--gos-col:{col}"'
        tiles += (
            f'<a href="{_s(a["link"])}" target="_blank" rel="noopener" '
            f'class="{cls}"{style}>'
            f'{media}'
            f'<span class="gos-src" style="background:{col}">{_s(a["source"])}</span>'
            f'<span class="gos-title">{_s(a["title"])}</span>'
            f'<span class="gos-time">{time_str}</span>'
            f'</a>\n'
        )
    if not tiles:
        tiles = '<p style="font-size:11px;color:var(--dim);padding:20px">No articles fetched.</p>'
    return (
        f'<div class="section gos-section">'
        f'<div class="sec-hd" style="border-top:2px solid #0C0C0C">'
        f'<span class="sec-hd-text">Opinions</span>'
        f'</div>'
        f'<div class="gos-grid">{tiles}</div>'
        f'</div>'
    )

def build_calendar_OLD(event_news={}):
    today     = datetime.now()
    today_str = today.strftime("%Y-%m-%d")

    cat_col = {
        "culture":"#3a3a3c","fashion":"#EA580C","football":"#15803D",
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
        "culture":"#3a3a3c","fashion":"#EA580C","football":"#15803D",
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
    tech_raw = _dedup_exact(_filter_recent(_fetch(TECH_SOURCES) + les_echos_tech + afp["tech"]))
    _ever = _fetch_evergreen(TECH_EVERGREEN)
    _ever_links = {a["link"] for a in _ever if a.get("link")}
    tech_raw = _ever + [a for a in tech_raw if a.get("link") not in _ever_links]
    print(f"    → {len(_ever)} evergreen ({', '.join(a['source'] for a in _ever) or 'none'})")
    # the Slow reads band renders picture cards, so those articles need images
    _slow = [a for a in tech_raw
             if SOURCE_CADENCE.get(a["source"]) == "slow" and not a.get("img")]
    if _slow:
        _backfill_images(_slow, limit=12)
    tech_grp = _dedup(tech_raw)
    print(f"    → {len(tech_raw)} articles → {len(tech_grp)} stories")
    print("  Fetching Macro…")
    les_echos_macro = _fetch_les_echos(LES_ECHOS_MACRO_KW, "macro")
    macro_raw = _dedup_exact(_filter_recent(
        _keep_block_daily(_fetch(MACRO_SOURCES)) + les_echos_macro + afp["macro"]))
    macro_grp = _dedup(macro_raw)
    print(f"    → {len(macro_raw)} articles → {len(macro_grp)} stories")
    print("  Fetching Culture…")
    art_newspaper_arts = _fetch_art_newspaper(5)
    nss_arts = _fetch_nss(10)
    print(f"    → {len(art_newspaper_arts)} NYT Arts + {len(nss_arts)} NSS articles (pinned)")
    culture_raw = _cap_per_source(_dedup_exact(_filter_recent(_fetch(CULTURE_SOURCES))))
    # Prepend pinned articles (NYT Arts + NSS) so they survive dedup and always appear
    pinned = art_newspaper_arts + nss_arts
    seen_links = {a["link"] for a in pinned if a.get("link")}
    culture_arts = pinned + [a for a in culture_raw if a.get("link") not in seen_links]
    # try for a real picture before falling back to a colour tile
    _backfill_images(culture_arts, limit=30)
    _c_img = sum(1 for a in culture_arts if a.get("img"))
    print(f"    → {len(culture_arts)} articles total, "
          f"{_c_img} with image / {len(culture_arts)-_c_img} colour tiles")
    print("  Fetching Sports…")
    sports_raw = _dedup_exact(_filter_recent(
        _fetch(SPORTS_SOURCES_FR) + _fetch(SPORTS_SOURCES_INT)
    ))
    sports_raw.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    print(f"    → {len(sports_raw)} articles (no grouping)")
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
    print("  Fetching Cities (City Focus)…")
    cities_raw = _filter_city_local(_dedup_exact(_filter_recent(_fetch(CITIES_SOURCES))))
    print(f"    → {len(cities_raw)} articles (no grouping)")
    print("  Fetching Opinions…")
    # Les Echos Idées now comes straight from its section feed in
    # GOSSIP_SOURCES_OTHER, so no keyword pass over the general feed is needed.
    _gossip_all = _dedup_exact(_fetch(GOSSIP_SOURCES_OTHER))
    # Per-source time window filtering
    _now_ts = datetime.now(timezone.utc).timestamp()
    gossip_raw = [
        a for a in _gossip_all
        if not a.get("ts") or
           a["ts"] >= _now_ts - GOSSIP_WINDOW_DAYS.get(a["source"], 7) * 86400
    ]
    gossip_raw.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    gossip_raw = _dedup_smart(gossip_raw)
    gossip_raw.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    gossip_raw = _cap_per_source(gossip_raw)   # newest N per source
    gossip_raw = gossip_raw[:40]
    _backfill_images(gossip_raw)
    _n_img = sum(1 for a in gossip_raw if a.get("img"))
    print(f"    → {len(gossip_raw)} gossip articles (after dedup), "
          f"{_n_img} with image / {len(gossip_raw)-_n_img} colour tiles")
    # The Polymarket and price bands were removed from the Markets page, so
    # their fetches are gone too — no point paying for data nothing renders.
    tech_grp  = _enrich_groups(tech_grp,  ai_client, headline_cache)
    macro_grp = _enrich_groups(macro_grp, ai_client, headline_cache)
    _save_headline_cache(headline_cache)
    print(f"    → {sum(1 for g in tech_grp+macro_grp if len(g)>1)} groups enriched")
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
    print("  Fetching Politico (geo panel)…")
    # Politico gets 48h; the weekly window is pinned to the same value so
    # Playbook Paris can't reach back further than the rest of the panel.
    geo_arts = _dedup_exact(_filter_recent(
        _keep_world_brief(_fetch(GEO_SOURCES)), days=2, weekly_days=2))
    # World in Brief is a daily — only ever show the current issue, not the
    # back catalogue Google News returns.
    _wib_cut = datetime.now(timezone.utc).timestamp() - 86400
    geo_arts = [a for a in geo_arts
                if a["source"] != "World in Brief"
                or (a.get("ts") and a["ts"] >= _wib_cut)]
    geo_arts.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc),
                  reverse=True)
    print(f"    → {len(geo_arts)} Politico articles")
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
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Morning Brief</title>
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#060606">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icons/icon-180.png">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Brief">
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
           json.dumps(conf_arts_js, ensure_ascii=False),
           geo_arts)}
</section>

<!-- ③ TECH + MACRO -->
<section class="snap-sec snap-feed">
  <div class="two-col">
{build_tech(tech_grp)}
{build_macro(macro_grp)}
  </div>
</section>

<!-- ④ CULTURE + EVENTS -->
<section class="snap-sec snap-culture">
{build_culture(culture_arts, event_news)}
</section>

<!-- ⑤ SPORTS + CITIES + PARIS -->
<section class="snap-sec snap-bottom">
  <div class="three-col">
{build_sports(sports_raw)}
{build_cities(cities_raw)}
{build_paris(paris_arts)}
  </div>
</section>

<!-- ⑥ GOSSIP -->
<section class="snap-sec snap-gossip">
{build_gossip(gossip_raw)}
</section>

<!-- Mobile bottom tab bar -->
{MOBILE_NAV}

<!-- Phone sizing -->
{PHONE_FIT_JS}

<!-- Unread dot tracker -->
<script>
(function(){{
  var KEY='mb_read';
  var read;
  try{{read=new Set(JSON.parse(localStorage.getItem(KEY)||'[]'));}}
  catch(e){{read=new Set();}}
  function save(){{
    try{{localStorage.setItem(KEY,JSON.stringify([...read].slice(-8000)));}}catch(e){{}}
  }}
  function addDot(a,isCard){{
    var url=a.getAttribute('href');
    if(!url||url==='#'||read.has(url)) return;
    var dot=document.createElement('span');
    dot.className='unread-dot';
    if(isCard){{a.appendChild(dot);}}
    else{{a.insertBefore(dot,a.firstChild);}}
    a.addEventListener('click',function(){{read.add(url);save();dot.remove();}},{{once:true}});
  }}
  // List-style article links
  document.querySelectorAll('.sg-title[target="_blank"],.pi-title[target="_blank"]').forEach(function(a){{addDot(a,false);}});
  // Card-style tiles
  document.querySelectorAll('.card[target="_blank"],.gos-card[target="_blank"]').forEach(function(a){{addDot(a,true);}});
}})();
</script>
</body>
</html>"""
    OUTPUT_FILE.write_text(page, encoding="utf-8")
    print("─"*54)
    print(f"✓  Saved → {OUTPUT_FILE}")
if __name__ == "__main__":
    main()
