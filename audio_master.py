import subprocess

def master_voice(raw_voice, out_voice):
    af_chain = (
        "highpass=f=85,lowpass=f=14500,afftdn=nf=-25,"
        "equalizer=f=120:t=q:w=1.2:g=3.5,equalizer=f=320:t=q:w=1.5:g=-2.5,"
        "equalizer=f=3500:t=q:w=1.5:g=3.0,"
        "acompressor=threshold=-18dB:ratio=3.5:attack=15:release=180:makeup=3,"
        "loudnorm=I=-14:TP=-1.5:LRA=7"
    )
    subprocess.run(["ffmpeg", "-y", "-i", raw_voice, "-af", af_chain, "-ar", "44100", "-ac", "2", "-b:a", "192k", out_voice], check=True)

def mix_voice_and_bgm(voice_path, bgm_path, final_audio_out):
    fc = (
        "[1:a]volume=0.22,aloop=loop=-1:size=2e+09[bgm];"
        "[bgm][0:a]sidechaincompress=threshold=0.03:ratio=6:attack=40:release=350[ducked];"
        "[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=2,loudnorm=I=-14:TP=-1.5:LRA=7[out]"
    )
    subprocess.run(["ffmpeg", "-y", "-i", voice_path, "-i", bgm_path, "-filter_complex", fc, "-map", "[out]", "-ar", "44100", "-ac", "2", "-b:a", "192k", final_audio_out], check=True)
