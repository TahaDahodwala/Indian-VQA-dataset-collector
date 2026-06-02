#!/usr/bin/env python3
"""
Indian Cultural VQA Dataset Collector
Sources: Wikimedia Commons (primary) + DuckDuckGo Images (fallback)

Usage:
  python collect_dataset.py                              # collect everything
  python collect_dataset.py --status                    # show per-item progress
  python collect_dataset.py --category folk_dance       # one category only
  python collect_dataset.py --category food --label "Dal Makhani"
  python collect_dataset.py --source ddg                # DuckDuckGo only
  python collect_dataset.py --source wikimedia          # Wikimedia only
"""

import argparse
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

import requests

# Ensure Unicode (arrows, Devanagari, etc.) prints cleanly on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── CONFIG ────────────────────────────────────────────────────────────────────

OUTPUT_DIR          = Path("dataset")
META_FILE           = OUTPUT_DIR / "metadata.json"
HASHES_FILE         = OUTPUT_DIR / "hashes.json"   # persists MD5s for dedup
MAX_IMAGES_PER_ITEM = 20
MIN_IMAGE_DIM       = 400   # skip images smaller than this in either dimension (px)

# Delays (seconds) — be polite
# Wikimedia CDN rate-limits bulk anonymous downloads aggressively.
# Use --source ddg to skip Wikimedia and avoid 429s entirely.
SLEEP_API_SEARCH    = 5.0   # between Wikimedia API calls
SLEEP_WM_DOWNLOAD   = 5.0   # between Wikimedia image downloads (strict CDN)
SLEEP_DDG_DOWNLOAD  = 2.0   # between DDG image downloads (more permissive)
SLEEP_JITTER        = 1.0

# Retry settings for 429 / 503
MAX_RETRIES      = 5
RETRY_BASE_DELAY = 30.0

# Valid raster extensions — fixes the old jpg/png-only counting bug
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# ── WIKIMEDIA CONFIG ──────────────────────────────────────────────────────────

WIKIMEDIA_API    = "https://commons.wikimedia.org/w/api.php"
ALLOWED_LICENSES = {
    "cc-by", "cc-by-sa", "cc0", "pd", "cc-by-2.0", "cc-by-sa-2.0",
    "cc-by-3.0", "cc-by-sa-3.0", "cc-by-4.0", "cc-by-sa-4.0",
    "public domain",
}

# ── FLICKR CONFIG ─────────────────────────────────────────────────────────────
# Get a free key at: https://www.flickr.com/services/apps/create
# Leave blank to skip Flickr entirely.

# FLICKR_API_KEY     = ""   # <── paste your key here
# FLICKR_API_SECRET  = ""
# FLICKR_ENDPOINT    = "https://www.flickr.com/services/rest/"
# FLICKR_CC_LICENSES = "1,2,4,5,9,10"   # BY-NC-SA, BY-NC, BY, BY-SA, CC0, PDM
# FLICKR_LICENSE_NAMES = {
#     "1": "CC BY-NC-SA 2.0", "2": "CC BY-NC 2.0",
#     "4": "CC BY 2.0",       "5": "CC BY-SA 2.0",
#     "9": "CC0 1.0",         "10": "PDM",
# }

# ── DUCKDUCKGO CONFIG ─────────────────────────────────────────────────────────
# No API key needed. Uses ddgs library.
# Install: pip install ddgs

from ddgs import DDGS

# ── DATASET ITEMS ─────────────────────────────────────────────────────────────

ITEMS = {
    "classical_dance": {
        "Bharatnatyam": "Bharatanatyam dancer performance",
        "Kathak":       "Kathak classical dance performance India",
        "Kathakali":    "Kathakali dancer costume makeup Kerala",
        "Kuchipudi":    "Kuchipudi dance performance Andhra Pradesh",
        "Odissi":       "Odissi classical dance performance Odisha",
        "Manipuri":     "Manipuri dance performance",
        "Sattriya":     "Sattriya dance performance Assam",
        "Mohiniyattam": "Mohiniyattam dance Kerala",
        "Chhau":        "Chhau dance mask performance Odisha",
    },
    "folk_dance": {
        "Bhangra":       "Bhangra folk dance Punjab performance",
        "Ghoomar":       "Ghoomar folk dance Rajasthan",
        "Kalbelia":      "Kalbelia dance performance Rajasthan",
        "Garba":         "Garba dance Gujarat Navratri",
        "Dandiya":       "Dandiya Raas Gujarat dance festival",
        "Lavani":        "Lavani folk dance Maharashtra performance",
        "Bihu":          "Bihu dance performance Assam",
        "Theyyam":       "Theyyam ritual dance Kerala",
        "Yakshagana":    "Yakshagana costume performance Karnataka",
        "Dollu Kunitha": "Dollu Kunitha drum dance Karnataka",
        "Cheraw":        "Cheraw bamboo dance Mizoram",
        "Pung Cholom":   "Pung Cholom drum dance Manipur",
        "Giddha": "Giddha folk dance Punjab women dance",
        "Cheraw"  :      "Cheraw folk dance mizoram",
    },
    "food": {
        "Dal Makhani":           "Dal Makhani Punjab food",
        "Makke di roti with Sarson da Saag": "Makke di roti with Sarson da Saag Punjab food",
        "Butter Chicken":        "Butter Chicken Punjabi curry",
        "Gushtaba":              "Gushtaba Kashmiri meatball curry",
        "Dum Aloo":              "Dum Aloo Kashmiri curry",
        "Kahwa tea":             "Kahwa tea Kashmiri tea",
        "Lal Maas":              "Lal Maas Rajasthan mutton curry",
        "Ker Suagri":            "Ker Sangri Rajasthan food",
        "Gatte ki Sabzi":        "Gatte ki Sabzi Rajasthan food",
        "Tunday Kebab":          "Tunday Kebab Lucknowi kebab",
        "Banarasi Chaat":        "Banarasi Chaat Varanasi street food",
        "Lucknowi Biryani":      "Lucknowi Biryani Awadhi rice dish",
        "Galouti Kebab":         "Galouti Kebab Lucknow kebab",
        "Thepla":                "Thepla Gujarati flatbread",
        "Fafda with Jalebi":     "Fafda with Jalebi Gujarat snack",
        "Handvo":                "Handvo Gujarati savory cake",
        "Misal Pav":             "Misal Pav Maharashtra street food",
        "Puran Poli":            "Puran Poli Maharashtra sweet flatbread",
        "Bebinca":               "Bebinca Goa dessert",
        "Fish Curry Rice":       "Fish Curry Rice Goa food",
        "Xacuti":                "Xacuti Goan curry",
        "Bajra Khichdi":         "Bajra Khichdi Rajasthan food",
        "Cholia":                "Cholia Uttarakhand food",
        "Rabadi":                "Rabadi dessert Rajasthan",
        "Chettinad Chicken":     "Chettinad Chicken Tamil Nadu curry",
        "Menduvada with Sambar":"Menduvada with Sambar South Indian food",
        "Idli with Sambhar":     "Idli with Sambhar South Indian breakfast",
        "Filter Coffee":         "Filter Coffee South Indian beverage",
        "Rasam":                 "Rasam South Indian soup",
        "Sambar":                "Sambar South Indian lentil stew",
        "Puttu Kadala":          "Puttu Kadala Kerala breakfast",
        "Karimeen Pollichathu":  "Karimeen Pollichathu Kerala fish dish",
        "Ghee Roast":            "Ghee Roast Mangalore food",
        "Ragi Mudde":            "Ragi Mudde Karnataka food",
        "Gongura Pachadi, , ":   "Gongura Pachadi Andhra Pradesh chutney",
        "Haleem":                "Haleem Hyderabadi dish",
        "Pesarattu":             "Pesarattu Andhra Pradesh dosa",
        "Mishti Doi":            "Mishti Doi Bengali sweet yogurt",
        "Kosha Mangsho":         "Kosha Mangsho Bengali mutton curry",
        "Ilish Bhapa":           "Ilish Bhapa Bengali fish dish",
        "Sattu Paratha":         "Sattu Paratha Bihar food",
        "Thekua":                "Thekua Bihar sweet",
        "Khaja":                 "Khaja Odisha sweet",
        "Dalma":                 "Dalma Odisha food",
        "Fish Orly":             "Fish Orly Goan fish fry",
        "Chhenapoda":            "Chhenapoda Odisha sweet",
        "Rasabali":              "Rasabali Odisha sweet",
        "Masor Tenga":           "Masor Tenga Assam fish curry",
        "Axone (fermented soy)": "Axone fermented soy Nagaland food",
        "Smoked meats":          "Smoked meats Northeast India food",
        "Pork with Bamboo Shoots":"Pork with Bamboo Shoots Northeast India food",
        "Gundruk Phagshapa":     "Gundruk Phagshapa Sikkim food",
        "Jadoh":                 "Jadoh Meghalaya rice dish",
        "Bitchi":                "Bitchi Chhattisgarh food",
        "Bhutte Ka Kees":        "Bhutte Ka Kees Madhya Pradesh snack",
        "Bafla":                 "Bafla Rajasthan food",
        "Lapsi":                 "Lapsi Rajasthan sweet",
        "Bafauri":               "Bafauri Chhattisgarh snack",
        "Red Ant Chutney":       "Red Ant Chutney tribal food",
        "Kusli":                "Kusli Chhattisgarh sweet",
        "Thekua":                "Thekua Bihar sweet",
        "Pua":                  "Pua Indian sweet",
        "Lobsters":              "Lobsters coastal Indian seafood",
        "Crabs":                 "Crabs coastal Indian seafood",
        "Prawns":                "Prawns coastal Indian seafood",
        "Ghee Roast":            "Ghee Roast South Indian food",
        "Rogan Josh":            "Rogan Josh Kashmiri lamb curry",
        "Dal Baati Churma":      "Dal Baati Churma Rajasthan food",
        "Chole Bhature":         "Chole Bhature Indian food",
        "Dhokla":               "Dhokla Gujarat snack",
        "Vindaloo":              "Vindaloo Goa pork curry",
        "Modak":                 "Modak sweet Maharashtra",
        "Masala Dosa":           "Masala Dosa South Indian breakfast",
        "Appam with Stew":       "Appam Kerala stew",
        "Sadya":                 "Kerala Sadya banana leaf feast",
        "Bisi Bele Bath":        "Bisi Bele Bath Karnataka",
        "Hyderabadi Biryani":    "Hyderabadi Biryani rice dish",
        "Mysore Pak":            "Mysore Pak sweet Karnataka",
        "Rosogulla":             "Rosogolla Bengali sweet",
        "Litti Chokha":          "Litti Chokha Bihar food",
        "Pitha":                 "Pitha Assamese rice cake",
        "Momos":                 "Momo dumpling Sikkim Nepal",
        "Thukpa":                "Thukpa noodle soup Sikkim",
        "Poha Jalebi":           "Poha Jalebi Madhya Pradesh breakfast",
    },
    "festivals": {
        "Pongal":           "Pongal festival Tamil Nadu celebration",
        "Onam":             "Onam festival Kerala boat race",
        "Baisakhi":         "Baisakhi festival Punjab celebration",
        "Ganesh Chaturthi": "Ganesh Chaturthi festival procession",
        "Durga Puja":       "Durga Puja pandal West Bengal",
        "Chhath Puja":      "Chhath Puja river Bihar worship",
        "Mysuru Dasara":    "Mysore Dasara procession Karnataka",
        "Hornbill Festival":"Hornbill festival Nagaland tribal",
        "Rakshabandhan":    "Rakshabandhan festival India",
        "Ugadi":            "Ugadi festival Andhra Pradesh Karnataka",
        "Vishu":            "Vishu festival Kerala celebration",
        "Thrissur Poonam":  "Thrissur Pooram festival Kerala",
        "Ramnavmi":         "Ram Navami festival India",
        "Rathyatra":        "Rathyatra chariot festival Odisha",
        "Janmashtami":      "Janmashtami festival India",
        "Dussehra":         "Dussehra festival India",
        "Guru Nanak Jayanti":"Guru Nanak Jayanti festival Sikh",
        "MahaShivratri":    "Maha Shivratri festival India",
        "Jhulan Yatra":     "Jhulan Yatra festival Bengal",
        "Gajan":            "Gajan festival Bengal",
        "Holi":             "Holi festival colors celebration India",
        "Navratri":         "Navratri Garba celebration Gujarat",
        "Rath Yatra":       "Rath Yatra chariot Puri Odisha",
        "Diwali":           "Diwali festival lights India",
        "Lohri":            "Lohri bonfire festival Punjab",
        "Makar Sankranti":  "Makar Sankranti kite festival India",
        "Gudi Padwa":       "Gudi Padwa festival Maharashtra",
        "Bihu":             "Bihu festival Assam celebration",
        "Gangaur":          "Gangaur festival Rajasthan women",
        "Lai Haraoba":      "Lai Haraoba festival Manipur",
    },
    "traditional_clothing_weaves": {
        "Patola":                 "Patola silk saree Gujarat weave",
        "Paithani":               "Paithani silk saree Maharashtra",
        "Sambalpuri":             "Sambalpuri weave saree Odisha",
        "Jamdani":                "Jamdani weave Bengal saree",
        "Chanderi":               "Chanderi fabric saree Madhya Pradesh",
        "Baluchari":              "Baluchari saree West Bengal",
        "Kota Doria":             "Kota Doria weave Rajasthan saree",
        "Ilkal":                  "Ilkal saree Karnataka weave",
        "Venkatagiri":            "Venkatagiri saree Andhra Pradesh",
        "Kannauri Shawl":         "Kannauri shawl Himachal Pradesh",
        "Bomkai":                 "Bomkai saree Odisha weave",
        "Tangalya":               "Tangaliya weave Gujarat craft",
        "Kimkhab":                "Kimkhab brocade Varanasi weave",
        "Himru":                  "Himru fabric Aurangabad weave",
        "Kanchi Pattu":           "Kancheepuram silk saree Tamil Nadu",
        "Kandagi":                "Ilkal Kandagi saree Karnataka",
        "Kanikachipuram":         "Kancheepuram silk saree Tamil Nadu",
        "Muga and Eri silk weaving":"Muga and Eri silk weaving Assam",
    },
    "traditional_clothing_embroidery": {
        "Zardosi":        "Zardosi embroidery gold thread India",
        "Kantha":         "Kantha embroidery Bengal stitch",
        "Chikankari":     "Chikankari embroidery Lucknow",
        "Phulkari":       "Phulkari embroidery Punjab dupatta",
        "Gota Patti":     "Gota Patti embroidery Rajasthan",
        "Aari":           "Aari embroidery India",
        "Sujani":         "Sujani embroidery Bihar",
        "Pipli Applique": "Pipli applique work Odisha",
        "Soof":           "Soof embroidery Gujarat Kutch",
        "Danka":          "Danka embroidery Gujarat",
        "Heer Bharat":    "Heer Bharat embroidery Sindh",
        "Pipli":          "Pipli applique work Odisha",
    },
    "traditional_clothing_prints": {
        "Bagru Print":     "Bagru block print Rajasthan fabric",
        "Sanganeri Print": "Sanganeri print fabric Rajasthan",
        "Ajrakh Print":    "Ajrakh block print Kutch Gujarat",
        "Kalamkari":       "Kalamkari hand painted fabric Andhra",
        "Bagh Print":      "Bagh print Madhya Pradesh fabric",
    },
    "religious_iconography": {
        "Lord Shiva":        "Lord Shiva idol sculpture temple",
        "Lord Vishnu":       "Lord Vishnu idol sculpture temple",
        "Goddess Durga":     "Goddess Durga idol sculpture",
        "Goddess Lakshmi":   "Goddess Lakshmi idol sculpture",
        "Goddess Kali":      "Goddess Kali idol sculpture Bengal",
        "Goddess Saraswati": "Goddess Saraswati idol sculpture",
        "Lord Brahma":       "Lord Brahma idol sculpture temple",
        "Om symbol":         "Om Aum symbol Hindu sacred",
        "Swastika Hindu":    "Hindu Swastika symbol auspicious",
        "Sri Chakra":        "Sri Chakra Yantra sacred geometry",
        "Tilak":             "Hindu Tilak forehead mark",
        "Rudraksha Mala":    "Rudraksha mala beads",
        "Vibhuti":           "Vibhuti sacred ash Hindu ritual",
        "Crescent Moon Star":"Islamic crescent moon star symbol mosque",
        "Christian Cross":   "Christian cross church India",
        "Jain Parsvanatha":  "Parsvanatha Jain Tirthankara idol",
        "Chaturvimsati":     "Chaturvimsati Jain Tirthankara idol",
        "Sikh Khanda":       "Sikh Khanda symbol Gurdwara",
        "Buddhist Mandala":  "Buddhist Mandala painting Tibet",
        "Dhyani Buddha":     "Five Dhyani Buddhas statue sculpture",
        "Lotus Buddhism":    "Lotus flower Buddhist temple",
    },
    "architecture": {
        "Red Fort Delhi":         "Red Fort Lal Qila Delhi",
        "Qutub Minar":            "Qutub Minar Delhi",
        "Humayuns Tomb":          "Humayun's Tomb Delhi",
        "India Gate":             "India Gate New Delhi",
        "Jama Masjid Delhi":      "Jama Masjid Delhi mosque",
        "Jantar Mantar":          "Jantar Mantar astronomical observatory Jaipur Rajasthan",
        "Taj Mahal":              "Taj Mahal Agra",
        "Agra Fort":              "Agra Fort Uttar Pradesh",
        "Fatehpur Sikri":         "Fatehpur Sikri Akbar palace",
        "Bara Imambara":          "Bara Imambara Lucknow Uttar Pradesh monument",
        "Chhota Imambara":        "Chhota Imambara Lucknow Uttar Pradesh monument",
        "Tomb of Itmad-Ud-Daula": "Tomb of Itmad-ud-Daulah Agra Uttar Pradesh monument",
        "Hawa Mahal":             "Hawa Mahal Jaipur Rajasthan",
        "Amber Fort":             "Amber Fort Jaipur Rajasthan",
        "Chittorgarh Fort":       "Chittorgarh Fort Rajasthan",
        "Jaisalmer Fort":         "Jaisalmer Fort Rajasthan desert fort",
        "Dilwara Jain Temple":    "Dilwara Jain Temple Mount Abu Rajasthan",
        "Ajanta Caves":           "Ajanta Caves Maharashtra",
        "Ellora Caves":           "Ellora Caves Maharashtra",
        "Gateway of India":       "Gateway of India Mumbai",
        "Elephanta Caves":        "Elephanta Caves Mumbai sculptures",
        "Bibi Ka Maqbara":        "Bibi Ka Maqbara Aurangabad Maharashtra monument",
        "Chhatrapati Shivaji Terminus":  "Chhatrapati Shivaji Terminus Mumbai Maharashtra railway station",
        "Brihadeeswarar Temple":  "Brihadeeswarar Temple Thanjavur",
        "Meenakshi Temple":       "Meenakshi Temple Madurai",
        "Mahabalipuram":          "Shore Temple Mahabalipuram Tamil Nadu",
        "Kailasanathar Temple":   "Kailasanathar Temple Kanchipuram Tamil Nadu",
        "St. George Fort":        "Fort St George Chennai Tamil Nadu colonial fort",
        "Hampi":                  "Hampi ruins Vijayanagara Karnataka",
        "Gol Gumbaz":             "Gol Gumbaz Bijapur Karnataka dome",
        "Mysore Palace":          "Mysore Palace Karnataka",
        "Lal Bagh":               "Lalbagh Botanical Garden Bengaluru Karnataka",
        "Daria Daulat Bagh":      "Daria Daulat Bagh Srirangapatna Karnataka palace",
        "Tipu Sultan Palace":     "Tipu Sultan Summer Palace Bengaluru Karnataka",
        "Khajuraho":              "Khajuraho temple sculptures Madhya Pradesh",
        "Sanchi Stupa":           "Sanchi Stupa Buddhist monument Madhya Pradesh",
        "Gwalior Fort":           "Gwalior Fort Madhya Pradesh hill fort",
        "Bagh Caves":             "Bagh Caves Madhya Pradesh rock cut caves",
        "Dhar Fort":              "Dhar Fort Madhya Pradesh fort architecture",
        "Sun Temple Konark":      "Sun Temple Konark Odisha",
        "Jagannath Temple":       "Jagannath Temple Puri Odisha",
        "Udayagiri and Khandagiri Caves":  "Udayagiri and Khandagiri Caves Odisha rock cut caves",
        "Lingaraj Temple":        "Lingaraj Temple Bhubaneswar Odisha temple",
        "Rani ki Vav":            "Rani ki Vav stepwell Patan Gujarat",
        "Golden Temple":          "Golden Temple Amritsar Punjab",
        "Sabarmati Ashram":       "Sabarmati Ashram Ahmedabad Gujarat Gandhi memorial",
        "Statue of Unity":        "Statue of Unity Gujarat monument",
        "Victoria Memorial":      "Victoria Memorial Kolkata",
        "Howrah Bridge":          "Howrah Bridge Kolkata",
        "Belur Math":             "Belur Math Howrah West Bengal monastery",
        "Shantiniketan":          "Shantiniketan Visva Bharati West Bengal heritage site",
        "Char Minar":             "Charminar Hyderabad Telangana",
        "Golconda Fort":          "Golconda Fort Hyderabad",
        "Makka Masjid":           "Makkah Masjid Hyderabad Telangana mosque",
        "Basilica Bom Jesus":     "Basilica of Bom Jesus Old Goa",
        "Sher Shah’s Tomb":       "Sher Shah Suri Tomb Sasaram Bihar monument",
        "Nalanda University":     "Nalanda University ruins Bihar archaeological site",
        "Gol Ghar":               "Golghar Patna Bihar granary monument",
        "Vishnupad Temple":       "Vishnupad Temple Gaya Bihar temple",
        "Golden Temple (Swarna Mandir)":    "Golden Temple Amritsar Punjab Sikh shrine",
        "Jallianwala Bagh":       "Jallianwala Bagh Amritsar Punjab memorial",
        "Shalimar Bagh":          "Shalimar Bagh Srinagar Kashmir Mughal garden",
        "Nishat Bagh":            "Nishat Bagh Srinagar Kashmir Mughal garden",
        "Leh Palace":             "Leh Palace Ladakh royal palace",
        "Basilica of Bom Jesus":  "Basilica of Bom Jesus Goa church",
        "St. Cathedral":          "Se Cathedral Old Goa church",
        "Bekal Fort":             "Bekal Fort Kerala coastal fort",
        "Group of Buddhist Monuments":  "Group of Buddhist Monuments Sanchi Madhya Pradesh",
        "Danteswari Temple":      "Danteswari Temple Dantewada Chhattisgarh temple",
        "Laxman Temple":          "Laxman Temple Sirpur Chhattisgarh temple",
        "Kangra Fort":            "Kangra Fort Himachal Pradesh fort",
        "Rock Cut Caves":         "Rock cut caves India ancient cave architecture",
        "Ahom Rajas Palace":      "Ahom Raja Palace Assam heritage architecture",
    },
    "handicrafts": {
        "Pashmina Shawl":       "Pashmina shawl Kashmir",
        "Papier Mache Kashmir": "Papier Mache Kashmir craft",
        "Kashmiri Carpets":     "Kashmiri carpet weaving Kashmir handicraft",
        "Banarasi silk sarees": "Banarasi silk saree Varanasi weaving",
        "Moradabad brassware":  "Moradabad brassware Uttar Pradesh handicraft",
        "Kancheepuram silk sarees": "Kancheepuram silk saree Tamil Nadu weaving",
        "Swamimalai bronze icons":  "Swamimalai bronze icons Tamil Nadu sculpture",
        "Terracotta art":   "Terracotta art Indian pottery handicraft",
        "Shantiniketan leather craft": "Shantiniketan leather craft West Bengal handicraft",
        "Sikki Grass Craft":    "Sikki grass craft Bihar handicraft",
        "Kathakali masks":      "Kathakali masks Kerala handicraft",
        "Aranmula mirrors":     "Aranmula mirror Kerala metal craft",
        "Coir crafts":          "Coir craft Kerala handicraft",
        "Applique work from Pipli": "Pipli applique work Odisha handicraft",
        "Bandhani tie-dye":     "Bandhani tie dye Rajasthan Gujarat textile",
        "Patola sarees":        "Patola silk saree Gujarat weaving",
        "Kullu shawls":         "Kullu shawl Himachal Pradesh weaving",
        "Kangra miniature paintings":   "Kangra miniature painting Himachal Pradesh art",
        "Chamba Rumal":         "Chamba Rumal embroidery Himachal Pradesh handicraft",
        "Paithani sarees":      "Paithani silk saree Maharashtra weaving",
        "Bidriware":            "Bidriware metal craft Karnataka handicraft",
        "Pochampalli Ikat":     "Pochampally Ikat Telangana weaving",
        "Kondapalli dolls":     "Kondapalli wooden dolls Andhra Pradesh handicraft",
        "Bastar Dhokra metal craft":    "Bastar Dhokra metal craft Chhattisgarh handicraft",
        "Blue Pottery":         "Blue Pottery Jaipur Rajasthan",
        "Meenakari Jewelry":    "Meenakari enamel jewelry Rajasthan",
        "Phad Painting":        "Phad painting Rajasthan scroll",
        "Sandalwood Carving":   "Sandalwood carving Karnataka",
        "Channapatna Toys":     "Channapatna wooden toys Karnataka",
        "Mysore Painting":      "Mysore traditional painting Karnataka",
        "Tanjore Painting":     "Tanjore painting Tamil Nadu gold",
        "Madhubani Painting":   "Madhubani painting Bihar folk art",
        "Pattachitra":          "Pattachitra painting Odisha",
        "Warli Painting":       "Warli painting Maharashtra tribal",
        "Kalamkari Painting":   "Kalamkari painting Andhra Pradesh",
        "Gond Painting":        "Gond painting Madhya Pradesh tribal",
        "Kutch Embroidery":     "Kutch embroidery Gujarat mirror work",
        "Kondapalli Dolls":     "Kondapalli wooden dolls Andhra Pradesh",
        "Aranmula Mirror":      "Aranmula metal mirror Kerala",
        "Jaapi Hat":            "Jaapi hat Assam bamboo",
        "Dhokra Art":           "Dhokra lost wax metal craft Odisha",
    },
    
}

# ── HTTP UTILS ────────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "IndianCulturalVQA/1.0 (research dataset)"})


def _jittered_sleep(base: float) -> None:
    t = base + random.uniform(-SLEEP_JITTER, SLEEP_JITTER)
    time.sleep(max(0.5, t))


def _request_with_backoff(method: str, url: str, **kwargs) -> requests.Response:
    """HTTP request with exponential back-off on 429 / 503.

    SSL errors (bad cert, hostname mismatch) are permanent — they are raised
    immediately so the caller can skip the URL rather than retrying uselessly.
    
    Connection reset/abort errors are treated as temporary failures — skip after
    2-3 attempts rather than retrying indefinitely.
    """
    delay = RETRY_BASE_DELAY
    connection_error_count = 0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = SESSION.request(method, url, **kwargs)
            if r.status_code in (429, 503):
                retry_after = r.headers.get("Retry-After")
                wait = min(float(retry_after), 120.0) if retry_after else delay
                print(f"  [RATE LIMIT {r.status_code}] waiting {wait:.0f}s "
                      f"(attempt {attempt}/{MAX_RETRIES})…")
                time.sleep(wait)
                delay *= 2
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.SSLError as e:
            # SSL failures (bad cert, hostname mismatch) are permanent — skip.
            print(f"  [SSL ERROR — skipping] {e}")
            raise
        except requests.exceptions.ConnectionError as e:
            connection_error_count += 1
            error_str = str(e).lower()
            # Connection reset/abort errors — skip after 2 attempts
            if "reset" in error_str or "aborted" in error_str or "10054" in str(e):
                if connection_error_count >= 2:
                    print(f"  [CONNECTION RESET — giving up after {connection_error_count} attempts]")
                    raise
                print(f"  [CONNECTION RESET] {e} — retry {connection_error_count}/2…")
                time.sleep(5)  # shorter delay for connection resets
                continue
            # Other connection errors — retry with backoff
            if "reset" in error_str or "abroted" in error_str or "11002" in str(e):
                if connection_error_count >= 2:
                    print(f"  [CONNECTOION ERROR - giving up after {connection_error_count} attempts]")
                    raise
                print(f"    [CONNECTION RESET] {e} - retry {connection_error_count}/2...")
                time.sleep(5)
                continue
            print(f"  [CONNECTION ERROR] {e} — retrying in {delay:.0f}s…")
            time.sleep(delay)
            delay *= 2
        except requests.exceptions.HTTPError:
            raise
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {url}")


# ── WIKIMEDIA FUNCTIONS ───────────────────────────────────────────────────────

def wm_search(query: str, limit: int = 30) -> list[dict]:
    params = {
        "action":              "query",
        "format":              "json",
        "generator":           "search",
        "gsrnamespace":        6,
        "gsrsearch":           query,
        "gsrlimit":            limit,
        "prop":                "imageinfo",
        "iiprop":              "url|extmetadata|size|mime",
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|ImageDescription",
    }
    try:
        r = _request_with_backoff("GET", WIKIMEDIA_API, params=params, timeout=20)
        pages = r.json().get("query", {}).get("pages", {})
        return list(pages.values())
    except Exception as e:
        error_msg = str(e).lower()
        if "rate" in error_msg or "429" in error_msg or "503" in error_msg:
            print(f"  ⚠️  [WIKIMEDIA RATE LIMIT] {e}")
            print(f"      Try again in ~5-10 minutes or use: python collect_dataset.py --source ddg")
        else:
            print(f"  [WIKIMEDIA API ERROR] {e}")
        return []


def wm_allowed_license(page: dict) -> bool:
    try:
        meta = page["imageinfo"][0]["extmetadata"]
        name = meta.get("LicenseShortName", {}).get("value", "").lower()
        return any(a in name for a in ALLOWED_LICENSES)
    except (KeyError, IndexError):
        return False


def wm_url(page: dict) -> str | None:
    try:
        return page["imageinfo"][0]["url"]
    except (KeyError, IndexError):
        return None


def wm_mime(page: dict) -> str:
    try:
        return page["imageinfo"][0].get("mime", "")
    except (KeyError, IndexError):
        return ""


def wm_dims(page: dict) -> tuple[int, int]:
    """Returns (width, height). Returns (0, 0) if unknown — skips size check."""
    try:
        info = page["imageinfo"][0]
        return info.get("width", 0), info.get("height", 0)
    except (KeyError, IndexError):
        return 0, 0


def wm_license(page: dict) -> str:
    try:
        meta = page["imageinfo"][0]["extmetadata"]
        return meta.get("LicenseShortName", {}).get("value", "unknown")
    except (KeyError, IndexError):
        return "unknown"


def wm_artist(page: dict) -> str:
    try:
        meta = page["imageinfo"][0]["extmetadata"]
        raw = meta.get("Artist", {}).get("value", "unknown")
        return re.sub(r"<[^>]+>", "", raw).strip()
    except (KeyError, IndexError):
        return "unknown"


# ── FLICKR FUNCTIONS ──────────────────────────────────────────────────────────

# def flickr_search(query: str, limit: int = 30) -> list[dict]:
#     """Returns list of Flickr photo dicts. Empty list if no key configured."""
#     if not FLICKR_API_KEY:
#         return []
#     params = {
#         "method":         "flickr.photos.search",
#         "api_key":        FLICKR_API_KEY,
#         "text":           query,
#         "license":        FLICKR_CC_LICENSES,
#         "media":          "photos",
#         "content_type":   1,        # photos only, no screenshots/illustrations
#         "per_page":       limit,
#         "sort":           "relevance",
#         "extras":         "url_l,url_o,license,owner_name,tags",
#         "format":         "json",
#         "nojsoncallback": 1,
#     }
#     try:
#         r = _request_with_backoff("GET", FLICKR_ENDPOINT, params=params, timeout=20)
#         return r.json().get("photos", {}).get("photo", [])
#     except Exception as e:
#         print(f"  [FLICKR API ERROR] {e}")
#         return []


# def flickr_url(photo: dict) -> str | None:
#     """Prefer original resolution, fall back to large (~1024px)."""
#     return photo.get("url_o") or photo.get("url_l") or None


# def flickr_license(photo: dict) -> str:
#     return FLICKR_LICENSE_NAMES.get(str(photo.get("license", "")), "unknown")

# ── DUCKDUCKGO FUNCTIONS ──────────────────────────────────────────────────────

def ddg_search(query: str, limit: int = 50) -> list[dict]:
    try:
        # ddgs 9.x: no context manager; first arg is positional 'query'
        return list(DDGS().images(
            query,
            region="in-en",
            safesearch="moderate",
            size="Large",           # skips small images — replaces MIN_IMAGE_DIM check
            # license_image="Share" removed: DDG's CC labels are unreliable and
            # drastically cut recall for niche Indian cultural topics.
            max_results=limit,
        ))
    except Exception as e:
        error_msg = str(e).lower()
        # Detect rate limiting
        if "rate" in error_msg or "limit" in error_msg or "429" in error_msg or "503" in error_msg:
            print(f"\n  ⚠️  [DDG RATE LIMIT] Hit search rate limit!")
            print(f"      Error: {e}")
            print(f"      ➜ Wait ~30-60 minutes before running this script again")
            print(f"      ➜ Or use: python collect_dataset.py --source wikimedia")
            print(f"         to skip DDG and use only Wikimedia\n")
            return []
        # Detect connection errors
        elif "connection" in error_msg or "timeout" in error_msg or "resolve" in error_msg:
            print(f"  ⚠️  [DDG CONNECTION ERROR] {e}")
            print(f"      Check your internet connection and try again in a few minutes\n")
            return []
        # Generic error
        else:
            print(f"  [DDG ERROR] {e}")
            return []


# ── COMMON UTILS ──────────────────────────────────────────────────────────────

def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def count_existing(item_dir: Path) -> int:
    """Count valid raster images already downloaded (all supported extensions)."""
    if not item_dir.exists():
        return 0
    return sum(1 for f in item_dir.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS)


def download_bytes(url: str) -> bytes | None:
    """Download raw bytes. Returns None on failure, skipping to next image."""
    try:
        r = _request_with_backoff("GET", url, timeout=30, stream=True)
        return r.content
    except requests.exceptions.Timeout:
        print(f"  [SKIP] Timeout downloading — moving to next image")
        return None
    except requests.exceptions.ConnectionError as e:
        error_str = str(e).lower()
        if "reset" in error_str or "aborted" in error_str or "10054" in str(e):
            print(f"  [SKIP] Connection reset by host — moving to next image")
        else:
            print(f"  [SKIP] Connection error: {e} — moving to next image")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"  [SKIP] HTTP error {e} — moving to next image")
        return None
    except RuntimeError as e:
        # Raised by _request_with_backoff after max retries
        print(f"  [SKIP] Download failed after retries — moving to next image")
        return None
    except Exception as e:
        print(f"  [SKIP] Download error: {e} — moving to next image")
        return None


def load_json(path: Path, default):
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  [WARN] {path.name} is corrupt ({e}); starting fresh.")
            path.rename(path.with_suffix(".json.bak"))
    return default


def save_json(path: Path, data) -> None:
    """Atomic write: write to .tmp then rename so a crash never corrupts the file."""
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)  # atomic on same filesystem


# ── URLS_FILE: Track downloaded URLs separately for easier dedup ────────────

URLS_FILE = OUTPUT_DIR / "downloaded_urls.json"

def load_urls_file() -> set:
    """Load set of already-downloaded URLs from persistent file."""
    if URLS_FILE.exists():
        try:
            with open(URLS_FILE, encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return set()
    return set()


def save_urls_file(seen_urls: set) -> None:
    """Persist all downloaded URLs to file."""
    save_json(URLS_FILE, list(seen_urls))


# ── STATUS REPORT ─────────────────────────────────────────────────────────────

def status_report(filter_category: str | None = None) -> None:
    """Print a per-item progress table."""
    total_items = total_collected = total_target = 0
    underfilled = []

    print(f"\n{'Category':<35} {'Label':<28} {'Have':>5} {'Need':>5}  Status")
    print("─" * 85)

    for category, items in ITEMS.items():
        if filter_category and category != filter_category:
            continue
        for label in items:
            safe     = label.replace(" ", "_").replace("/", "-")
            item_dir = OUTPUT_DIR / category / safe
            have     = count_existing(item_dir)
            need     = MAX_IMAGES_PER_ITEM
            if have >= need:
                status = "✓ done"
            elif have > 0:
                status = f"◑ {have}/{need}"
            else:
                status = "✗ empty"
            print(f"  {category:<33} {label:<28} {have:>5} {need:>5}  {status}")
            total_items     += 1
            total_collected += have
            total_target    += need
            if have < need:
                underfilled.append((category, label, have))

    print("─" * 85)
    print(f"  Total: {total_collected}/{total_target} images across {total_items} items")
    if underfilled:
        print(f"\n  Underfilled ({len(underfilled)} items):")
        for cat, lbl, have in underfilled:
            print(f"    • {cat}/{lbl}  ({have}/{MAX_IMAGES_PER_ITEM})")
    print()


# ── MAIN COLLECTOR ────────────────────────────────────────────────────────────

def collect(
    source: str = "both",
    filter_category: str | None = None,
    filter_label: str | None = None,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    metadata    = load_json(META_FILE,    default={})
    seen_hashes = set(load_json(HASHES_FILE, default=[]))
    seen_urls   = load_urls_file()  # Load from dedicated URLs file
    
    print(f"[INFO] Loaded {len(seen_urls)} previously downloaded URLs")
    print(f"[INFO] Loaded {len(seen_hashes)} previously seen image hashes\n")

    # if source == "flickr" and not FLICKR_API_KEY:
    #     print("[ERROR] FLICKR_API_KEY is not set. Edit the script and add your key.")
    #     return

    for category, items in ITEMS.items():
        if filter_category and category != filter_category:
            continue

        cat_dir = OUTPUT_DIR / category
        cat_dir.mkdir(exist_ok=True)

        for label, query in items.items():
            if filter_label and label != filter_label:
                continue

            safe_label = label.replace(" ", "_").replace("/", "-")
            item_dir   = cat_dir / safe_label
            item_dir.mkdir(exist_ok=True)

            downloaded = count_existing(item_dir)
            if downloaded >= MAX_IMAGES_PER_ITEM:
                print(f"[SKIP] {category}/{label} — {downloaded} images already collected")
                continue

            print(f"\n[FETCH] {category}/{label}  →  '{query}'")

            # ── Wikimedia pass ────────────────────────────────────────────────
            if source in ("wikimedia", "both"):
                _jittered_sleep(SLEEP_API_SEARCH)
                pages = wm_search(query, limit=30)

                for page in pages:
                    if downloaded >= MAX_IMAGES_PER_ITEM:
                        break

                    try:
                        if not wm_allowed_license(page):
                            continue

                        mime = wm_mime(page)
                        if not mime.startswith("image/") or mime in ("image/svg+xml", "image/tiff"):
                            continue

                        # Size filter — skip thumbnails / tiny images
                        # If dims are unknown (0,0), let it through
                        w, h = wm_dims(page)
                        if w and h and (w < MIN_IMAGE_DIM or h < MIN_IMAGE_DIM):
                            continue

                        url = wm_url(page)
                        if not url:
                            continue
                        
                        # Skip if URL already downloaded
                        if url in seen_urls:
                            print(f"  [SKIP] URL already downloaded (from another item/category)")
                            continue

                        ext = "." + url.split(".")[-1].split("?")[0].lower()
                        if ext not in IMAGE_EXTENSIONS:
                            continue

                        filename = f"{safe_label}_{downloaded + 1:03d}{ext}"
                        filepath = item_dir / filename

                        if filepath.exists():
                            downloaded += 1
                            continue

                        _jittered_sleep(SLEEP_WM_DOWNLOAD)
                        data = download_bytes(url)
                        if not data:
                            continue

                        image_hash = md5(data)
                        if image_hash in seen_hashes:
                            print(f"  [DUPE] skipping duplicate (same image content)")
                            continue

                        filepath.write_bytes(data)
                        seen_hashes.add(image_hash)
                        seen_urls.add(url)  # Flag this URL as downloaded

                        lic = wm_license(page)
                        metadata[f"{category}/{label}/{filename}"] = {
                            "category": category,
                            "label":    label,
                            "filename": filename,
                            "source":   "wikimedia",
                            "url":      url,
                            "license":  lic,
                            "artist":   wm_artist(page),
                            "query":    query,
                            "md5":      image_hash,
                        }
                        downloaded += 1
                        print(f"  ✓ [WM] {filename}  [{lic}]")
                        
                        # Persist after every image to prevent re-downloading duplicates
                        save_json(META_FILE, metadata)
                        save_json(HASHES_FILE, list(seen_hashes))
                        save_urls_file(seen_urls)
                    except Exception as e:
                        # Skip this image and continue to the next
                        print(f"  [SKIP] Error processing Wikimedia image: {e}")
                        continue

            # ── Flickr fallback (triggered only when Wikimedia comes up short) ─
            # if source in ("flickr", "both") and downloaded < MAX_IMAGES_PER_ITEM:
            #     if not FLICKR_API_KEY:
            #         if source == "both":
            #             print(f"  [INFO] Flickr key not set — skipping fallback for '{label}'")
            #     else:
            #         if source == "both":
            #             print(f"  [FLICKR] only {downloaded}/{MAX_IMAGES_PER_ITEM} from Wikimedia,"
            #                   f" trying Flickr…")
            #         _jittered_sleep(SLEEP_API_SEARCH)
            #         photos = flickr_search(query, limit=30)

            #         for photo in photos:
            #             if downloaded >= MAX_IMAGES_PER_ITEM:
            #                 break

            #             url = flickr_url(photo)
            #             if not url:
            #                 continue

            #             raw_ext = "." + url.split("?")[0].rsplit(".", 1)[-1].lower()
            #             ext     = raw_ext if raw_ext in IMAGE_EXTENSIONS else ".jpg"

            #             filename = f"{safe_label}_fl_{downloaded + 1:03d}{ext}"
            #             filepath = item_dir / filename

            #             if filepath.exists():
            #                 downloaded += 1
            #                 continue

            #             _jittered_sleep(SLEEP_DOWNLOAD)
            #             data = download_bytes(url)
            #             if not data:
            #                 continue

            #             image_hash = md5(data)
            #             if image_hash in seen_hashes:
            #                 print(f"  [DUPE] skipping duplicate")
            #                 continue

            #             filepath.write_bytes(data)
            #             seen_hashes.add(image_hash)

            #             lic = flickr_license(photo)
            #             metadata[f"{category}/{label}/{filename}"] = {
            #                 "category": category,
            #                 "label":    label,
            #                 "filename": filename,
            #                 "source":   "flickr",
            #                 "url":      url,
            #                 "license":  lic,
            #                 "artist":   photo.get("owner_name", "unknown"),
            #                 "query":    query,
            #                 "md5":      image_hash,
            #             }
            #             downloaded += 1
            #             print(f"  ✓ [FL] {filename}  [{lic}]")
            
            # ── DuckDuckGo fallback ───────────────────────────────────────────
            if source in ("ddg", "both") and downloaded < MAX_IMAGES_PER_ITEM:
                print(f"  [DDG] {downloaded}/{MAX_IMAGES_PER_ITEM} so far, trying DuckDuckGo…")
                time.sleep(2)  # small pause before DDG call
                results = ddg_search(query, limit=30)

                for result in results:
                    if downloaded >= MAX_IMAGES_PER_ITEM:
                        break

                    try:
                        url = result.get("image")
                        if not url:
                            continue

                        # Skip if URL already downloaded
                        if url in seen_urls:
                            print(f"  [SKIP] URL already downloaded (from another item/category)")
                            continue

                        # DDG often returns Wikimedia /thumb/ URLs which reject non-standard
                        # sizes with 400. Convert to the canonical full-resolution URL.
                        if "upload.wikimedia.org" in url and "/thumb/" in url:
                            base, rest = url.split("/thumb/", 1)
                            # rest = "hash1/hash2/file.jpg/390px-file.jpg" — drop last segment
                            path_no_size = rest.rsplit("/", 1)[0]
                            url = f"{base}/{path_no_size}"
                        
                        # Check again after URL normalization
                        if url in seen_urls:
                            print(f"  [SKIP] URL already downloaded (normalized)")
                            continue

                        raw_ext = "." + url.split("?")[0].rsplit(".", 1)[-1].lower()
                        ext     = raw_ext if raw_ext in IMAGE_EXTENSIONS else ".jpg"

                        filename = f"{safe_label}_ddg_{downloaded + 1:03d}{ext}"
                        filepath = item_dir / filename

                        if filepath.exists():
                            downloaded += 1
                            continue

                        _jittered_sleep(SLEEP_DDG_DOWNLOAD)
                        data = download_bytes(url)
                        if not data:
                            continue

                        image_hash = md5(data)
                        if image_hash in seen_hashes:
                            print(f"  [DUPE] skipping duplicate (same image content)")
                            continue

                        filepath.write_bytes(data)
                        seen_hashes.add(image_hash)
                        seen_urls.add(url)  # Flag this URL as downloaded

                        metadata[f"{category}/{label}/{filename}"] = {
                            "category": category,
                            "label":    label,
                            "filename": filename,
                            "source":   f"ddg/{result.get('source', 'unknown')}",
                            "url":      url,
                            "license":  "CC (Share)",
                            "artist":   result.get("title", "unknown"),
                            "query":    query,
                            "md5":      image_hash,
                        }
                        downloaded += 1
                        print(f"  ✓ [DDG] {filename}  via {result.get('source', '?')}")
                        
                        # Persist after every image to prevent re-downloading duplicates
                        save_json(META_FILE, metadata)
                        save_json(HASHES_FILE, list(seen_hashes))
                        save_urls_file(seen_urls)
                    except Exception as e:
                        # Skip this image and continue to next
                        print(f"  [SKIP] Error processing DDG image: {e}")
                        continue

            # ── DDG retry with shorter query if still underfilled ─────────────
            if source in ("ddg", "both") and downloaded < MAX_IMAGES_PER_ITEM:
                # Build a shorter fallback query (first 2–3 words of the original)
                short_query = " ".join(query.split()[:3])
                if short_query != query:
                    print(f"  [DDG retry] still {downloaded}/{MAX_IMAGES_PER_ITEM},"
                          f" trying shorter query: '{short_query}'…")
                    time.sleep(2)
                    results2 = ddg_search(short_query, limit=50)

                    for result in results2:
                        if downloaded >= MAX_IMAGES_PER_ITEM:
                            break

                        try:
                            url = result.get("image")
                            if not url:
                                continue

                            # Skip if URL already downloaded
                            if url in seen_urls:
                                print(f"  [SKIP] URL already downloaded (retry)")
                                continue

                            if "upload.wikimedia.org" in url and "/thumb/" in url:
                                base, rest = url.split("/thumb/", 1)
                                path_no_size = rest.rsplit("/", 1)[0]
                                url = f"{base}/{path_no_size}"

                            # Check again after URL normalization
                            if url in seen_urls:
                                print(f"  [SKIP] URL already downloaded (normalized, retry)")
                                continue

                            raw_ext = "." + url.split("?")[0].rsplit(".", 1)[-1].lower()
                            ext     = raw_ext if raw_ext in IMAGE_EXTENSIONS else ".jpg"

                            filename = f"{safe_label}_ddg_{downloaded + 1:03d}{ext}"
                            filepath = item_dir / filename

                            if filepath.exists():
                                downloaded += 1
                                continue

                            _jittered_sleep(SLEEP_DDG_DOWNLOAD)
                            data = download_bytes(url)
                            if not data:
                                continue

                            image_hash = md5(data)
                            if image_hash in seen_hashes:
                                print(f"  [DUPE] skipping duplicate")
                                continue

                            filepath.write_bytes(data)
                            seen_hashes.add(image_hash)
                            seen_urls.add(url)  # Flag this URL as downloaded

                            metadata[f"{category}/{label}/{filename}"] = {
                                "category": category,
                                "label":    label,
                                "filename": filename,
                                "source":   f"ddg/{result.get('source', 'unknown')}",
                                "url":      url,
                                "license":  "unknown",
                                "artist":   result.get("title", "unknown"),
                                "query":    short_query,
                                "md5":      image_hash,
                            }
                            downloaded += 1
                            print(f"  ✓ [DDG2] {filename}  via {result.get('source', '?')}")
                            
                            # Persist after every image to prevent re-downloading duplicates
                            save_json(META_FILE, metadata)
                            save_json(HASHES_FILE, list(seen_hashes))
                            save_urls_file(seen_urls)
                        except Exception as e:
                            # Skip this image and continue to next
                            print(f"  [SKIP] Error processing DDG image: {e}")
                            continue

            # Persist after every label so a crash loses at most one item
            save_json(META_FILE,    metadata)
            save_json(HASHES_FILE,  list(seen_hashes))
            save_urls_file(seen_urls)

            final = count_existing(item_dir)
            if final < MAX_IMAGES_PER_ITEM:
                print(f"  [WARN] {label}: only {final}/{MAX_IMAGES_PER_ITEM} collected")

    # ── Post-run summary ──────────────────────────────────────────────────────
    print("\n" + "═" * 52)
    print("  COLLECTION COMPLETE — Per-category summary")
    print("═" * 52)

    grand_total = 0
    for category, items in ITEMS.items():
        if filter_category and category != filter_category:
            continue
        cat_have   = sum(count_existing(OUTPUT_DIR / category /
                         lbl.replace(" ", "_").replace("/", "-"))
                         for lbl in items)
        cat_target = len(items) * MAX_IMAGES_PER_ITEM
        grand_total += cat_have
        bar = "✓" if cat_have >= cat_target else "◑"
        print(f"  {bar}  {category:<40} {cat_have:>4}/{cat_target}")

    print("─" * 52)
    print(f"     Grand total: {grand_total} images\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Indian Cultural VQA Dataset Collector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python collect_dataset.py                              collect everything
  python collect_dataset.py --status                    show per-item progress
  python collect_dataset.py --category folk_dance       one category only
  python collect_dataset.py --category food --label "Dal Makhani"
  python collect_dataset.py --source ddg                DuckDuckGo only
  python collect_dataset.py --source wikimedia          Wikimedia only
        """,
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print per-item progress table and exit (no downloading)",
    )
    parser.add_argument(
        "--category", type=str, default=None,
        metavar="CATEGORY",
        help=f"Collect only this category. Choices: {', '.join(ITEMS.keys())}",
    )
    parser.add_argument(
        "--label", type=str, default=None,
        metavar="LABEL",
        help="Collect only this label (requires --category)",
    )
    parser.add_argument(
        "--source", type=str, default="both",
        choices=["wikimedia", "ddg", "both"],
        help="Image source to use (default: both — Wikimedia first, DuckDuckGo as fallback)",
    )
    args = parser.parse_args()

    if args.label and not args.category:
        parser.error("--label requires --category")

    if args.category and args.category not in ITEMS:
        parser.error(
            f"Unknown category '{args.category}'. "
            f"Valid: {', '.join(ITEMS.keys())}"
        )

    if args.status:
        status_report(filter_category=args.category)
        return

    collect(
        source=args.source,
        filter_category=args.category,
        filter_label=args.label,
    )


if __name__ == "__main__":
    main()
