import uuid, os
from monure_unchained import MonureParser, MidiExporter, render_audio

TEMP_PATH = "temp"
OUT_AUDIO_PATH = "out"

# 基础文件夹检查
for p in [TEMP_PATH, OUT_AUDIO_PATH]:
    if not os.path.exists(p):
        os.makedirs(p)
        print(f"Folder ready: {p}")

if __name__ == "__main__":
    # 混合记谱实验：BASE_PITCH 偏移 + 6TET (全音阶律)
    # 在 6TET 下，3K1 到 4K1 只有 6 个音。
    test_data = """D5 A5 B5 4 A5 4 G5 A5 G5 F5 D5 16
D5 A5 B5 4 A5 4 G5 A5 C6 D6 B5 16

Rest # 休止
R 64 # 休止

C D E F G A B
C5 C6 C7 D D D D7
    """

    SF2_PATH = "SteinwayBPrelube.sf2"
    parser = MonureParser()
    parser.parse(test_data)
    print(parser.show())
    
    full_data = parser.get_full_struct()
    config = full_data[0]["config"]
    
    id_tag = str(uuid.uuid4())[:8]
    midi_tmp = f"{TEMP_PATH}/tmp_{id_tag}.mid"
    audio_out = f"{OUT_AUDIO_PATH}/monure_anarchy_{id_tag}.wav"

    exporter = MidiExporter(div=config["DIV"])
    exporter.export(full_data, midi_tmp)

    if os.path.exists(SF2_PATH):
        render_audio(midi_tmp, SF2_PATH, audio_out, 
                     base_pitch=config["BASE_PITCH"], 
                     tuning=config["TUNING"])
    
    if os.path.exists(midi_tmp): os.remove(midi_tmp)