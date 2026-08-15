# test_valorant_profile.py
import sys
import json
from pathlib import Path

# Windows konsol kodlaması (Türkçe karakterler için)
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except:
        pass

def test_profile():
    profile_path = Path("profiles/valorant.json")
    
    # 1. Dosya var mı?
    assert profile_path.exists(), "Dosya bulunamadı!"
    
    # 2. JSON geçerli mi?
    with open(profile_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 3. Gerekli alanlar var mı?
    required = ['profile_name', 'color_detection', 'aim_settings', 
                'recoil_settings', 'keybinds']
    for field in required:
        assert field in data, f"{field} eksik!"
    
    # 4. Renk aralıkları doğru mu?
    ranges = data['color_detection']['ranges']
    assert len(ranges) == 2, "İki renk aralığı olmalı"
    
    # 5. Silah pattern'leri var mı?
    weapons = data['recoil_settings']['weapons']
    assert 'vandal' in weapons, "Vandal pattern'i eksik"
    assert 'phantom' in weapons, "Phantom pattern'i eksik"
    
    print("[OK] Tüm testler başarılı!")
    print(f"Profil: {data['profile_name']}")
    print(f"Aim modu: {data['aim_settings']['mode']}")
    print(f"Silah sayısı: {len(weapons)}")

if __name__ == "__main__":
    test_profile()