using System;

// The audited decorative bindings only. Icons, portrait layers, fills and effects
// never enter this list. Kept free of Unity dependencies for compatibility checks.
internal static class NativeUIThemeRules
{
    internal enum Role { None, Menu, Skill, Window, ChatInput, Ornament, Bar }

    internal static Role Resolve(string sprite, string path, string scope)
    {
        if (string.IsNullOrEmpty(sprite) || string.IsNullOrEmpty(path)) return Role.None;
        if (scope == "hud")
        {
            if (path.Contains("/PlayerPanel/") || path.Contains("/PortraitLayer/")) return Role.None;
            if (sprite == "MiniMenuBG_0" && path.Contains("/MiniMenu/") && path.EndsWith("/Graphics/BG", StringComparison.Ordinal)) return Role.Menu;
            if (sprite == "SkillButton_Frame" && path.Contains("/SlotsContainer/") && path.EndsWith("/ButtonGraphic/Frame", StringComparison.Ordinal)) return Role.Skill;
            if (path.Contains("/XPBars/") && (sprite == "BarFillBackgroundSmall" || sprite == "Button Grey") && path.EndsWith("/Background", StringComparison.Ordinal)) return Role.Bar;
        }
        if (scope == "chat" && sprite == "InputFieldFloatingBG" && path.EndsWith("/UIChatBody/InputField (TMP)", StringComparison.Ordinal)) return Role.ChatInput;
        if (scope == "window")
        {
            if (sprite == "9 Slice Main Window Background Round" && (path == "Image" || path == "WindowBG" || path == "WindowContainer/Image" || path == "GameObject/WindowContainer" || path == "GameObject/WindowContainer/WindowBG")) return Role.Window;
            // Direct window ornaments only: never recolor tab selections or item rarity.
            string ornamentPath = path.StartsWith("GameObject/WindowContainer/", StringComparison.Ordinal) ? path.Substring("GameObject/WindowContainer/".Length) : path;
            if (!ornamentPath.Contains("/") && (sprite == "Window Corner Bottom Left" || sprite == "Dragon Corner" || sprite == "Dragon Top Left")) return Role.Ornament;
            if (ornamentPath == "Header" && sprite == "9 Slice Tab") return Role.Ornament;
        }
        return Role.None;
    }
}
