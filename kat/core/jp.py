# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Offline Japanese to English for the names MMD models use. Pure Python, no network."""
import re
import unicodedata

TERMS = {
    # body and bones
    "全ての親": "root", "センター": "center", "グルーブ": "groove", "腰": "waist", "下半身": "lower_body",
    "上半身": "upper_body", "首": "neck", "頭": "head", "肩": "shoulder", "腕": "arm", "ひじ": "elbow",
    "手首": "wrist", "手": "hand", "親指": "thumb", "人差指": "index", "人指": "index", "中指": "middle",
    "薬指": "ring", "小指": "little", "足首": "ankle", "ひざ": "knee", "足": "leg", "つま先": "toe",
    "両目": "eyes", "目": "eye", "舌": "tongue", "髪": "hair", "前髪": "bangs", "後ろ髪": "back_hair",
    "横髪": "side_hair", "尻尾": "tail", "耳": "ear", "スカート": "skirt", "袖": "sleeve", "リボン": "ribbon",
    "胸": "chest", "ネクタイ": "tie", "マント": "cape", "帽子": "hat", "足ＩＫ": "leg_ik", "足IK": "leg_ik",
    "つま先ＩＫ": "toe_ik", "つま先IK": "toe_ik", "ＩＫ": "ik", "IK": "ik",
    # position words
    "前": "front", "後ろ": "back", "後": "back", "横": "side", "上": "upper", "下": "lower", "先": "tip",
    "根元": "root", "中": "mid",
    # face morphs
    "まばたき": "blink", "ウィンク": "wink", "にこり": "smile", "笑い": "laugh", "怒り": "angry",
    "困る": "troubled", "悲しい": "sad", "驚き": "surprised", "びっくり": "surprised", "ジト目": "sleepy_eyes",
    "瞳小": "pupil_small", "照れ": "blush", "口角上げ": "mouth_corner_up", "口角下げ": "mouth_corner_down",
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o", "ん": "n",
    # materials
    "顔": "face", "肌": "skin", "体": "body", "白目": "eye_white", "瞳": "pupil", "ハイライト": "highlight",
    "眉": "eyebrow", "まつげ": "eyelash", "口": "mouth", "歯": "teeth", "服": "clothes", "上着": "jacket",
    "靴": "shoes", "靴下": "socks", "手袋": "gloves", "影": "shadow", "アクセサリ": "accessory",
}
_ORDER = sorted(TERMS, key=len, reverse=True)
VOWELS = {"あ", "い", "う", "え", "お", "ん"}  # inside a longer word these are just syllables
CJK = re.compile(r"[぀-ヿ一-鿿＀-￯]")


def has_japanese(name):
    return bool(CJK.search(name))


def translate(name):
    """'左髪1' -> 'hair1.L'. Terms we do not know stay as they are, so nothing is silently lost."""
    if not has_japanese(name):
        return name
    text = unicodedata.normalize("NFKC", name)
    side = ""
    if "左" in text:
        side, text = ".L", text.replace("左", "")
    elif "右" in text:
        side, text = ".R", text.replace("右", "")
    pieces, i = [], 0
    while i < len(text):
        hit = next((t for t in _ORDER if text.startswith(t, i)
                    and (t not in VOWELS or (i == 0 and text[i + 1:].isdigit() or i == 0 and len(text) == 1))), None)
        if hit:
            pieces.append(TERMS[hit])
            i += len(hit)
            continue
        run = re.match(r"\d+|[^\W\d]+|\W+", text[i:])
        chunk = run.group()
        if pieces and (chunk.isdigit() or not chunk[0].isalnum()) and chunk.strip(" _-"):
            pieces[-1] += chunk  # digits and punctuation hang on the word before them
        elif chunk.strip(" _-"):
            pieces.append(chunk)
        i += len(chunk)
    return "_".join(pieces) + side if pieces else name
