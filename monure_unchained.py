import uuid
import os
import re
import math
import subprocess
from mido import Message, MidiFile, MidiTrack, MetaMessage

TEMP_PATH = "temp"

# 基础文件夹检查
for p in [TEMP_PATH]:
    if not os.path.exists(p):
        os.makedirs(p)
        print(f"Folder ready: {p}")

# =================================================================
# 1. Monure 叛逆解析器 (支持 n-TET 与 NKm 记谱)
# =================================================================
class MonureParser:
    def __init__(self):
        self.default_config = {
            "BPM": 130, 
            "DIV": 8, 
            "DEF_LEN": 8, 
            "VOL": 80,
            "BASE_PITCH": 440.0,
            "TUNING": "12TET"  # 支持 "12TET", "6TET", "19TET", "JUST" 等
        }
        self.patterns = []
        self.total_ticks = 0
        self.current_track_name = "Default"
        self.current_pattern = None
        self.in_meta_block = False
        self.cursor = 0

    def _ensure_pattern(self):
        if self.current_pattern is None:
            self.current_pattern = {
                "config": self.default_config.copy(),
                "tracks": {}, 
                "start_offset": 0
            }
            self.patterns.append(self.current_pattern)
            self.cursor = 0

    def _ensure_track(self, name):
        if name not in self.current_pattern["tracks"]:
            self.current_pattern["tracks"][name] = {"meta": {}, "notes": []}

    def _switch_pattern(self):
        if self.current_pattern is None:
            self._ensure_pattern()
        else:
            self.total_ticks += self.current_pattern["config"]["DIV"]
            self.current_pattern = {
                "config": self.default_config.copy(),
                "tracks": {},
                "start_offset": self.total_ticks
            }
            self.patterns.append(self.current_pattern)
            self.cursor = 0

    def _get_current_tet(self):
        """解析 TUNING 字符串获取 n 值"""
        t = self.current_pattern["config"]["TUNING"]
        # 支持 "19TET", "19K12", "19EDO" 等格式提取数字
        match = re.search(r"(\d+)", t)
        return int(match.group(1)) if match else 12

    def pitch_to_midi(self, pitch_str):
        if pitch_str in ["R", "REST"]: return "REST"
        
        # --- 核心修改：nKm 解析系统 ---
        # 匹配 nKm 格式：数字 + K + 数字 (如 4K1, 3K12)
        monure_match = re.match(r"(\d+)K(\d+)", pitch_str)
        if monure_match:
            octave = int(monure_match.group(1)) # 前面的 n (音高/va)
            step = int(monure_match.group(2))   # 后面的 m (当前八度内的第几阶)
            n = self._get_current_tet()
            
            # MIDI 转换公式：(八度 + 1) * 律制总阶数 + (步阶 - 1)
            # 这里的 midi_key 只是一个“虚拟槽位”，后续渲染器会根据 n 重新赋予物理频率
            midi_val = (octave + 1) * n + (step - 1)
            return midi_val

        # 匹配传统记谱 (仅建议在 12TET 下使用)
        names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        match = re.match(r"([A-G][#b]?)(\-?\d*)", pitch_str)
        if not match: return None
        name, oct_str = match.groups()
        octave = int(oct_str) if oct_str != "" else 5
        if 'b' in name:
            m = {'Db':'C#','Eb':'D#','Gb':'F#','Ab':'G#','Bb':'A#'}
            name = m.get(name, name)
        return (octave + 1) * 12 + names.index(name)

    def _clamp_vol(self, v):
        try:
            val = int(float(v))
            return min(max(val, 0), 100)
        except: return 100

    def parse(self, text):
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'): continue
            if line == "<": self.in_meta_block = True; continue
            if line == ">": self.in_meta_block = False; continue
            
            if self.in_meta_block:
                if ":" in line:
                    k, v = [x.strip() for x in line.split(':', 1)]
                    self._ensure_track(self.current_track_name)
                    self.current_pattern["tracks"][self.current_track_name]["meta"][k] = v
                continue

            if line.lower().startswith('[pattern]'):
                self._switch_pattern()
                continue

            if ":" in line and not line.startswith('['):
                k, v = [x.strip().upper() for x in line.split(':', 1)]
                if k in self.default_config:
                    if k == "BASE_PITCH": val = float(v)
                    elif k == "TUNING": val = v # 如 19K12
                    else: val = int(v)
                    self.default_config[k] = val
                    if self.current_pattern: self.current_pattern["config"][k] = val
                continue

            track_match = re.match(r"\[\d+:(.+?)\]", line)
            if track_match:
                self._ensure_pattern()
                self.current_track_name = track_match.group(1).strip()
                self._ensure_track(self.current_track_name)
                self.cursor = 0
                continue

            self._ensure_pattern()
            self._ensure_track(self.current_track_name)
            tokens = line.split()
            t_idx = 0
            while t_idx < len(tokens):
                token = tokens[t_idx]
                pos = None
                if token.isdigit():
                    pos = int(token); t_idx += 1
                    if t_idx >= len(tokens): break
                    token = tokens[t_idx]

                duration = self.current_pattern["config"]["DEF_LEN"]
                velocity = self.current_pattern["config"]["VOL"]
                
                offset = 1
                while t_idx + offset < len(tokens) and tokens[t_idx+offset].replace('.','',1).isdigit():
                    val_str = tokens[t_idx+offset]
                    if offset == 1: duration = int(float(val_str))
                    elif offset == 2: velocity = int(min(float(val_str) * velocity * 0.01, 100))
                    offset += 1

                rel_pos = pos if pos is not None else self.cursor
                abs_tick = self.current_pattern["start_offset"] + rel_pos
                
                # 处理和弦 _
                p_list = token.split('_')
                if self.pitch_to_midi(p_list[0]) is not None:
                    for p in p_list:
                        midi = self.pitch_to_midi(p)
                        self.current_pattern["tracks"][self.current_track_name]["notes"].append({
                            "abs_tick": abs_tick, "note": midi, "len": duration, "vol": velocity
                        })
                    self.cursor = rel_pos + duration
                    t_idx += offset 
                else: t_idx += 1

    def get_full_struct(self): return self.patterns

    def show(self):
        out_str = ""
        for i, p in enumerate(self.patterns):
            if not p["tracks"]: continue
            out_str += f"\n" + "="*60 + "\n"
            conf = p['config']
            out_str += f" [Monure Pattern {i+1}] Start: {p['start_offset']} | Tuning: {conf['TUNING']} | A4: {conf['BASE_PITCH']}Hz\n"
            out_str += "="*60 + "\n"
            for tname, tdata in p["tracks"].items():
                out_str += f"\n  >> Track: [{tname}]\n" 
                for n in tdata["notes"][:3]:
                    out_str += f"     Tick: {n['abs_tick']:<5} | MidiKey: {n['note']:<4} | Vol: {n['vol']}\n"
        return out_str

# =================================================================
# 2. MIDI 导出器 (保持逻辑，但增加对非 12 律 MIDI 键的支持)
# =================================================================
class MidiExporter:
    def __init__(self, div=128):
        self.mid = MidiFile()
        self.mid.ticks_per_beat = div

    def export(self, patterns, filename="output.mid"):
        combined_tracks = {}
        bpm = patterns[0]["config"].get("BPM", 120) if patterns else 120

        for p in patterns:
            for tname, tdata in p["tracks"].items():
                if tname not in combined_tracks: combined_tracks[tname] = []
                combined_tracks[tname].extend(tdata["notes"])

        for tname, notes in combined_tracks.items():
            track = MidiTrack()
            self.mid.tracks.append(track)
            track.append(MetaMessage('track_name', name=tname))
            import mido
            track.append(MetaMessage('set_tempo', tempo=mido.bpm2tempo(bpm)))

            events = []
            for n in notes:
                if str(n["note"]).upper() in ["REST", "R"]: continue
                vel = int(min(n["vol"] * 1.27, 127))
                # 限制 MIDI 键位在 0-127
                m_key = max(0, min(int(n["note"]), 127))
                events.append({"tick": n["abs_tick"], "type": "note_on", "note": m_key, "vel": vel})
                events.append({"tick": n["abs_tick"] + n["len"], "type": "note_off", "note": m_key, "vel": 0})

            events.sort(key=lambda x: (x["tick"], x["type"] == "note_on"))
            last_tick = 0
            for e in events:
                delta = e["tick"] - last_tick
                track.append(Message(e["type"], note=e["note"], velocity=e["vel"], time=delta))
                last_tick = e["tick"]
        self.mid.save(filename)

# =================================================================
# 3. 叛逆渲染器 (物理重写任意 n-TET 频率)
# =================================================================
def render_audio(midi_path, sf2_path, output_path, base_pitch=440.0, tuning="12TET"):
    id_4 = str(uuid.uuid4()).replace('-', '_')
    tuning_script = f"{TEMP_PATH}/nkm_tune_{id_4}.txt"
    
    # 解析律制阶数 n
    tet_n = 12
    match = re.search(r"(\d+)", tuning)
    if match: tet_n = int(match.group(1))

    # 基准 A4 偏移计算
    # 在 n 律下，MIDI 69 对应的音分值依然是 6900 (作为物理中心)
    pitch_offset_cents = 1200.0 * math.log2(base_pitch / 440.0)
    a4_anchor_cents = 6900.0 + pitch_offset_cents

    with open(tuning_script, "w") as f:
        f.write(f"tuning monure_nkm 0 0\n")
        
        # 核心映射：重新定义 128 个 MIDI 键位对应的物理音分
        for key in range(128):
            # 公式：以 MIDI 69 为原点
            # 目标音分 = (当前键位 - 69) * (1200 / 律制阶数) + A4锚定点
            final_pitch = (key - 69) * (1200.0 / tet_n) + a4_anchor_cents
            
            # 物理限制检查
            if final_pitch < 0: final_pitch = 0
            if final_pitch > 12700: final_pitch = 12700
            
            f.write(f"tune 0 0 {key} {round(final_pitch, 4)}\n")
        
        for chan in range(16):
            f.write(f"settuning {chan} 0 0\n")

    print(f">>> Monure Unchained Engine: [n={tet_n} | A={base_pitch}Hz]")
    cmd = ["fluidsynth", "-ni", "-g", "1.2", "-f", tuning_script, "-F", output_path, sf2_path, midi_path]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"Success: {output_path}")
    finally:
        if os.path.exists(tuning_script): os.remove(tuning_script)        
