using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

// No Unity objects are constructed at Doorstop boot. The manager is created by
// HUDCanvas.Start, after Unity and the native HUD have initialized.
internal sealed class NativeUIThemes : MonoBehaviour
{
    private static readonly string[] Names = { "Default", "DoomKnight", "Chaos", "BLOD" };
    private static readonly string[] Keys = { "default", "doomknight", "chaos", "blod" };
    private static NativeUIThemes current;
    private readonly Dictionary<Image, Binding> bindings = new Dictionary<Image, Binding>();
    private readonly Dictionary<string, Sprite> sprites = new Dictionary<string, Sprite>();
    private readonly Dictionary<Sprite, Sprite> originals = new Dictionary<Sprite, Sprite>();
    private readonly List<Texture2D> textures = new List<Texture2D>();
    private readonly List<Image> dead = new List<Image>();
    private readonly List<Dropdown> pickers = new List<Dropdown>();
    private readonly List<Text> requirements = new List<Text>();
    private int selected;
    private int characterId;
    private int achievementBits;
    private bool accessInitialized;
    private bool updatingPickers;

    private sealed class Binding
    {
        internal Image Image;
        internal string AuditedName;
        internal NativeUIThemeRules.Role Role;
        internal Sprite Original, Applied;
        internal Color OriginalColor, AppliedColor;
        internal bool Tinted;
    }

    internal static void Install(Harmony harmony)
    {
        Patch(harmony, typeof(HUDCanvas), "Start", nameof(HUDReady));
        Patch(harmony, typeof(Image), "OnEnable", nameof(ImageEnabled));
        Patch(harmony, typeof(UIOptionsManager), "Awake", nameof(OptionsReady));
        // The confirmed reset callback, not the method that opens a confirmation.
        Patch(harmony, typeof(UIOptionsManager), "DoResetToDefaults", nameof(ResetConfirmed));
    }

    private static void Patch(Harmony harmony, Type type, string name, string postfix)
    {
        var method = AccessTools.DeclaredMethod(type, name);
        if (method == null) { InfinityLoaderMod.SafeLog("[ui-skins] missing hook " + type.Name + "." + name); return; }
        harmony.Patch(method, postfix: new HarmonyMethod(typeof(NativeUIThemes), postfix));
    }

    private static void HUDReady(HUDCanvas __instance)
    {
        try
        {
            if (current != null) return;
            current = __instance.gameObject.AddComponent<NativeUIThemes>();
            current.RefreshAccess();
            current.ScanScene();
            InfinityLoaderMod.SafeLog("[ui-skins] native HUD ready; theme=" + Names[current.selected]);
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[ui-skins] HUD setup failed: " + ex.Message); }
    }

    private static void ImageEnabled(Image __instance)
    {
        if (current == null) return;
        try { current.Register(__instance); }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[ui-skins] binding failed: " + ex.Message); }
    }

    private static void OptionsReady(UIOptionsManager __instance)
    {
        if (current == null) return; // Login options retain the unmodified native UI.
        try { current.AddPicker(__instance); current.ScanScene(); }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[ui-skins] picker failed: " + ex.Message); }
    }

    private static void ResetConfirmed()
    {
        if (current != null) current.Select(0);
    }

    private void ScanScene()
    {
        foreach (var image in Resources.FindObjectsOfTypeAll<Image>())
            if (image != null && image.gameObject.scene.IsValid() && image.gameObject.scene.isLoaded) Register(image);
    }

    private void Register(Image image)
    {
        if (image == null || !image.gameObject.scene.IsValid() || !image.gameObject.scene.isLoaded || bindings.ContainsKey(image) || image.sprite == null) return;
        // Resolving ancestry is limited to the few audited sprite identities.
        string name = image.sprite.name;
        if (name != "MiniMenuBG_0" && name != "SkillButton_Frame" && name != "9 Slice Main Window Background Round"
            && name != "InputFieldFloatingBG" && name != "Window Corner Bottom Left" && name != "Dragon Corner"
            && name != "Dragon Top Left" && name != "9 Slice Tab" && name != "BarFillBackgroundSmall" && name != "Button Grey") return;
        string scope = null, path = null;
        var hud = image.GetComponentInParent<HUDCanvas>(true);
        var chat = image.GetComponentInParent<UIChat>(true);
        var window = image.GetComponentInParent<UIWindow>(true);
        if (chat != null) { scope = "chat"; path = PathFrom(image.transform, chat.transform, true); }
        else if (window != null) { scope = "window"; path = PathFrom(image.transform, window.transform, false); }
        else if (hud != null) { scope = "hud"; path = PathFrom(image.transform, hud.transform, true); }
        else
        {
            var hero = image.GetComponentInParent<UIYourHero>(true);
            if (hero != null) { scope = "window"; path = PathFrom(image.transform, hero.transform, false); }
        }
        var role = NativeUIThemeRules.Resolve(name, path, scope);
        if (role == NativeUIThemeRules.Role.None) return;
        var binding = new Binding { Image = image, Role = role, AuditedName = name, Original = image.sprite, OriginalColor = image.color };
        bindings.Add(image, binding);
        Apply(binding);
    }

    private static string PathFrom(Transform child, Transform root, bool includeRoot)
    {
        var parts = new List<string>();
        for (var t = child; t != null && t != root; t = t.parent) parts.Add(t.name);
        if (includeRoot) parts.Add(root.name);
        parts.Reverse();
        return string.Join("/", parts);
    }

    private void LateUpdate()
    {
        RefreshAccess();
        // Only the registered decorative images, not a scan of every UI element
        // each frame. If the game changes a native sprite, remember that state.
        dead.Clear();
        foreach (var pair in bindings)
        {
            var b = pair.Value;
            if (b.Image == null) { dead.Add(pair.Key); continue; }
            if ((b.Applied != null && b.Image.sprite != b.Applied) || (b.Applied == null && b.Image.sprite != b.Original))
            {
                Sprite original;
                b.Original = b.Image.sprite != null && originals.TryGetValue(b.Image.sprite, out original) ? original : b.Image.sprite;
                b.Applied = null;
                Apply(b);
            }
            if (b.Tinted && b.Image.color != b.AppliedColor)
            {
                // Preserve native alpha fades and state transitions; tint only RGB.
                Apply(b);
            }
        }
        foreach (var image in dead) bindings.Remove(image);
    }

    private static bool SameRGB(Color a, Color b)
    {
        return Mathf.Approximately(a.r, b.r) && Mathf.Approximately(a.g, b.g) && Mathf.Approximately(a.b, b.b);
    }

    private void Select(int index)
    {
        RefreshAccess();
        var info = Entity.myPlayerData == null ? null : Entity.myPlayerData.Info;
        selected = NativeUIThemeAccess.ResolveSelection(index, characterId > 0 && info != null ? info.achievements : null);
        string preference = NativeUIThemeAccess.PreferenceKey(characterId);
        if (preference != null)
        {
            PlayerPrefs.SetInt(preference, selected);
            PlayerPrefs.Save();
        }
        foreach (var binding in bindings.Values) if (binding.Image != null) Apply(binding);
        ScanScene();
        updatingPickers = true;
        pickers.RemoveAll(picker => picker == null);
        foreach (var picker in pickers) picker.SetValueWithoutNotify(selected);
        updatingPickers = false;
        InfinityLoaderMod.SafeLog("[ui-skins] selected " + Names[selected] + "; decorative bindings=" + bindings.Count);
    }

    private void RefreshAccess()
    {
        var info = Entity.myPlayerData == null ? null : Entity.myPlayerData.Info;
        int id = info == null ? 0 : info.CharID;
        int bits = 0;
        if (info != null && info.achievements != null) info.achievements.TryGetValue(NativeUIThemeAccess.AchievementField, out bits);
        if (accessInitialized && characterId == id && achievementBits == bits) return;
        accessInitialized = true;
        bool differentCharacter = characterId != id;
        characterId = id;
        achievementBits = bits;
        string preference = NativeUIThemeAccess.PreferenceKey(id);
        int saved = differentCharacter && preference != null ? PlayerPrefs.GetInt(preference, 0) : selected;
        selected = NativeUIThemeAccess.ResolveSelection(saved, id > 0 && info != null ? info.achievements : null);
        // A locked or unauthenticated character can never inherit another character's skin.
        foreach (var binding in bindings.Values) if (binding.Image != null) Apply(binding);
        RefreshPickers();
    }

    private List<string> PickerNames()
    {
        var info = Entity.myPlayerData == null ? null : Entity.myPlayerData.Info;
        var labels = new List<string>();
        for (int i = 0; i < Names.Length; i++)
            labels.Add(Names[i] + (NativeUIThemeAccess.IsUnlocked(i, characterId > 0 && info != null ? info.achievements : null) ? "" : " (Locked)"));
        return labels;
    }

    private void RefreshPickers()
    {
        updatingPickers = true;
        pickers.RemoveAll(picker => picker == null);
        foreach (var picker in pickers)
        {
            picker.Hide();
            picker.ClearOptions();
            picker.AddOptions(PickerNames());
            picker.SetValueWithoutNotify(selected);
        }
        requirements.RemoveAll(label => label == null);
        string message = (achievementBits & (1 << NativeUIThemeAccess.AchievementBit)) != 0 && characterId > 0
            ? NativeUIThemeAccess.AchievementName + " earned."
            : "Earn " + NativeUIThemeAccess.AchievementName + ": redeem border.";
        foreach (var label in requirements) label.text = message;
        updatingPickers = false;
    }

    private void Apply(Binding b)
    {
        // A native state may change immediately before a theme selection. Capture
        // it now as well as in LateUpdate, so Default always preserves that state.
        if (b.Image.sprite != b.Applied && b.Image.sprite != b.Original)
        {
            Sprite original;
            b.Original = b.Image.sprite != null && originals.TryGetValue(b.Image.sprite, out original) ? original : b.Image.sprite;
        }
        bool tinted = b.Role == NativeUIThemeRules.Role.Ornament || b.Role == NativeUIThemeRules.Role.Bar;
        if (b.Tinted)
        {
            Color changed = b.Image.color;
            if (SameRGB(changed, b.AppliedColor))
            {
                b.OriginalColor.a = changed.a;
                b.Image.color = b.OriginalColor;
            }
            else b.OriginalColor = changed;
        }
        b.Tinted = false;
        if (selected == 0)
        {
            if (b.Image.sprite == b.Applied) b.Image.sprite = b.Original;
            b.Applied = null;
            return;
        }
        if (b.Original == null || b.Original.name != b.AuditedName) return;
        if (tinted)
        {
            Color tint = selected == 1 ? new Color(0.8f, 0.28f, 0.28f) : selected == 2 ? new Color(0.68f, 0.38f, 1f) : Color.white;
            b.AppliedColor = b.OriginalColor * tint;
            b.Image.color = b.AppliedColor;
            b.Tinted = true;
            return;
        }
        string asset = b.Role == NativeUIThemeRules.Role.Menu ? "menu" : b.Role == NativeUIThemeRules.Role.Skill ? "skill" : "window";
        // Do not replace an unknown native state with an unrelated decoration.
        string expected = b.Role == NativeUIThemeRules.Role.Menu ? "MiniMenuBG_0" : b.Role == NativeUIThemeRules.Role.Skill ? "SkillButton_Frame"
            : b.Role == NativeUIThemeRules.Role.ChatInput ? "InputFieldFloatingBG" : "9 Slice Main Window Background Round";
        if (b.Original.name != expected) return;
        b.Applied = Replacement(b.Original, asset);
        if (b.Applied != null) b.Image.sprite = b.Applied;
    }

    private Sprite Replacement(Sprite original, string asset)
    {
        string key = Keys[selected] + "/" + asset + "/" + original.GetInstanceID();
        Sprite result;
        if (sprites.TryGetValue(key, out result)) return result;
        Texture2D source = null, packed = null;
        try
        {
            string resource = "InfinityLoader.UIThemes." + Keys[selected] + "." + asset + ".png";
            using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(resource))
            {
                if (stream == null) throw new FileNotFoundException(resource);
                byte[] bytes;
                using (var buffer = new MemoryStream()) { stream.CopyTo(buffer); bytes = buffer.ToArray(); }
                source = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                if (!ImageConversion.LoadImage(source, bytes, false)) throw new InvalidDataException(resource);
            }
            int w = Mathf.RoundToInt(original.rect.width), h = Mathf.RoundToInt(original.rect.height);
            if (w < 1 || h < 1 || w > 2048 || h > 2048 || (asset == "menu" && (w != 190 || h != 190)))
                throw new InvalidDataException("Native sprite geometry changed; retaining original " + original.name);
            packed = new Texture2D(w, h, TextureFormat.RGBA32, false);
            packed.name = "InfinitySkin." + key;
            packed.filterMode = FilterMode.Bilinear;
            packed.wrapMode = TextureWrapMode.Clamp;
            var pixels = new Color[w * h];
            // Match visible artwork rather than generated PNG canvas padding.
            // Native packed export:181x167; alpha>=128 bbox:[2,2,178,164].
            // Sprite coordinates use bottom-left, exported PNG coordinates top-left.
            Rect content = asset == "menu"
                ? new Rect(5.07612f + 180.84775f * 2f / 181f, 11.07612f + 166.84775f * 3f / 167f, 180.84775f * 176f / 181f, 166.84775f * 162f / 167f)
                : new Rect(0, 0, w, h);
            Rect sourceContent = asset == "menu" ? OpaqueBounds(source) : new Rect(0, 0, source.width, source.height);
            for (int y = 0; y < h; y++) for (int x = 0; x < w; x++)
            {
                float u = (sourceContent.x + (x + 0.5f - content.x) / content.width * sourceContent.width) / source.width;
                float v = (sourceContent.y + (y + 0.5f - content.y) / content.height * sourceContent.height) / source.height;
                if (u >= 0 && u <= 1 && v >= 0 && v <= 1) pixels[y * w + x] = source.GetPixelBilinear(u, v);
            }
            packed.SetPixels(pixels);
            packed.Apply(false, false); // Native alpha hit testing may read pixels.
            result = Sprite.Create(packed, new Rect(0, 0, w, h), new Vector2(original.pivot.x / original.rect.width, original.pivot.y / original.rect.height), original.pixelsPerUnit, 0, SpriteMeshType.FullRect, original.border);
            result.name = "InfinitySkin." + Keys[selected] + "." + original.name;
            sprites.Add(key, result);
            originals.Add(result, original);
            textures.Add(packed);
            return result;
        }
        catch (Exception ex)
        {
            if (packed != null) Destroy(packed);
            sprites[key] = null; // Fail once; retain the native sprite.
            InfinityLoaderMod.SafeLog("[ui-skins] asset failed " + key + ": " + ex.Message);
            return null;
        }
        finally { if (source != null) Destroy(source); }
    }

    private static Rect OpaqueBounds(Texture2D source)
    {
        var pixels = source.GetPixels32();
        int left = source.width, bottom = source.height, right = -1, top = -1;
        for (int y = 0; y < source.height; y++) for (int x = 0; x < source.width; x++)
        {
            if (pixels[y * source.width + x].a < 128) continue;
            left = Math.Min(left, x); right = Math.Max(right, x);
            bottom = Math.Min(bottom, y); top = Math.Max(top, y);
        }
        if (right < left || top < bottom) throw new InvalidDataException("No opaque menu artwork");
        return new Rect(left, bottom, right - left + 1, top - bottom + 1);
    }

    private void AddPicker(UIOptionsManager options)
    {
        if (options.transform.Find("InfinityUIThemeRow") != null) return;
        var template = options.fpsDropdown;
        if (template == null || template.captionText == null) return;
        // Append to a native layout container. Refuse arbitrary coordinates if a
        // future client changes the options hierarchy.
        Transform parent = template.transform.parent;
        while (parent != null && parent != options.transform && parent.GetComponent<VerticalLayoutGroup>() == null) parent = parent.parent;
        if (parent == null || parent == options.transform) { InfinityLoaderMod.SafeLog("[ui-skins] no native options layout; picker skipped"); return; }
        if (parent.Find("InfinityUIThemeRow") != null) return;
        var row = new GameObject("InfinityUIThemeRow", typeof(RectTransform), typeof(LayoutElement));
        row.SetActive(false);
        row.transform.SetParent(parent, false);
        float height = Mathf.Max(55, ((RectTransform)template.transform).rect.height);
        row.GetComponent<LayoutElement>().preferredHeight = height + 28;
        row.GetComponent<LayoutElement>().minHeight = height + 28;
        var labelGO = new GameObject("Label", typeof(RectTransform), typeof(Text));
        labelGO.transform.SetParent(row.transform, false);
        var label = labelGO.GetComponent<Text>();
        label.font = template.captionText.font;
        label.fontSize = template.captionText.fontSize;
        label.fontStyle = template.captionText.fontStyle;
        label.color = template.captionText.color;
        label.alignment = TextAnchor.MiddleLeft;
        label.text = "UI theme";
        label.raycastTarget = false;
        SetRect(label.rectTransform, 0, 0.53f, 6, -6, 0.30f);
        var requirementGO = new GameObject("UnlockRequirement", typeof(RectTransform), typeof(Text));
        requirementGO.transform.SetParent(row.transform, false);
        var requirement = requirementGO.GetComponent<Text>();
        requirement.font = label.font;
        requirement.fontSize = Mathf.Max(12, label.fontSize - 3);
        requirement.color = label.color;
        requirement.alignment = TextAnchor.MiddleLeft;
        requirement.raycastTarget = false;
        SetRect(requirement.rectTransform, 0, 1, 6, -6, 0, 0.30f);
        requirements.Add(requirement);
        var pickerGO = Instantiate(template.gameObject, row.transform, false);
        pickerGO.name = "UIThemeDropdown";
        // Clone the native Dropdown appearance, not its settings callbacks.
        foreach (var behaviour in pickerGO.GetComponentsInChildren<MonoBehaviour>(true))
        {
            string ns = behaviour.GetType().Namespace ?? "";
            if (ns != "UnityEngine.UI" && ns != "UnityEngine.EventSystems" && ns != "TMPro") { behaviour.enabled = false; Destroy(behaviour); }
        }
        var picker = pickerGO.GetComponent<Dropdown>();
        picker.onValueChanged = new Dropdown.DropdownEvent();
        picker.navigation = new Navigation { mode = Navigation.Mode.Automatic };
        picker.ClearOptions();
        picker.AddOptions(PickerNames());
        picker.SetValueWithoutNotify(selected);
        SetRect((RectTransform)picker.transform, 0.55f, 1, 0, -6, 0.30f);
        picker.onValueChanged.AddListener(index => { if (!updatingPickers) Select(index); });
        pickers.Add(picker);
        RefreshPickers();
        pickerGO.SetActive(true);
        row.SetActive(true);
        LayoutRebuilder.MarkLayoutForRebuild(parent as RectTransform);
    }

    private static void SetRect(RectTransform rect, float left, float right, float leftPadding, float rightPadding, float bottom = 0, float top = 1)
    {
        rect.anchorMin = new Vector2(left, bottom);
        rect.anchorMax = new Vector2(right, top);
        rect.offsetMin = new Vector2(leftPadding, 4);
        rect.offsetMax = new Vector2(rightPadding, -4);
        rect.localScale = Vector3.one;
    }

    private void OnDestroy()
    {
        selected = 0;
        foreach (var binding in bindings.Values) if (binding.Image != null) Apply(binding);
        foreach (var sprite in sprites.Values) if (sprite != null) Destroy(sprite);
        foreach (var texture in textures) if (texture != null) Destroy(texture);
        bindings.Clear();
        sprites.Clear();
        originals.Clear();
        if (current == this) current = null;
    }
}
