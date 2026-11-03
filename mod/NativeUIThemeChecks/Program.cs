using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Security.Cryptography;
using System.Text.Json;
using Role = NativeUIThemeRules.Role;

int checks = 0;
void Assert(bool ok, string message) { if (!ok) throw new Exception(message); checks++; }
void Binding(string sprite, string path, string scope, Role expected) => Assert(NativeUIThemeRules.Resolve(sprite, path, scope) == expected, $"Incorrect binding: {path} => {sprite}");
var repo = Directory.GetCurrentDirectory();
// Locked saved choices and missing login data fail closed, while unrelated
// founder achievements cannot unlock this collection.
var locked = new Dictionary<string, int> { ["ip25"] = -1 };
var unlocked = new Dictionary<string, int> { [NativeUIThemeAccess.AchievementField] = 1 };
var otherBit = new Dictionary<string, int> { [NativeUIThemeAccess.AchievementField] = 2 };
foreach (var index in new[] { 1, 2, 3 })
{
    Assert(!NativeUIThemeAccess.IsUnlocked(index, null!), "Missing identity unlocked a theme");
    Assert(NativeUIThemeAccess.ResolveSelection(index, locked) == 0, "Locked saved selection must restore Default");
    Assert(!NativeUIThemeAccess.IsUnlocked(index, otherBit), "Wrong achievement bit unlocked a theme");
    Assert(NativeUIThemeAccess.ResolveSelection(index, unlocked) == index, "Collection achievement did not unlock theme");
}
Assert(NativeUIThemeAccess.IsUnlocked(0, null!), "Default must always be available");
Assert(NativeUIThemeAccess.ResolveSelection(99, unlocked) == 0, "Unknown saved theme must restore Default");
Assert(NativeUIThemeAccess.ResolveSelection(-1, unlocked) == 0, "Negative saved theme must restore Default");
Assert(NativeUIThemeAccess.PreferenceKey(1) != NativeUIThemeAccess.PreferenceKey(2), "Theme selections crossed character identities");
Assert(NativeUIThemeAccess.PreferenceKey(0) == null, "Unauthenticated selection must not persist");
Binding("MiniMenuBG_0", "HUDCanvasPC/SafeArea/MiniMenu/Button Options/Graphics/BG", "hud", Role.Menu);
Binding("MiniMenuBG_1", "HUDCanvasPC/SafeArea/MiniMenu/Button Options/Graphics/WhiteOutline", "hud", Role.None);
Binding("MiniMenuIcon_Settings_Gear", "HUDCanvasPC/SafeArea/MiniMenu/Button Options/Graphics/Icon", "hud", Role.None);
Binding("MiniMenuBG_0", "HUDCanvasPC/SafeArea/PlayerPanel/PortraitLayer/Debug Stuff/Opened/ButtonClearApop_DEV/Graphics/BG", "hud", Role.None);
Binding("SkillButton_Frame", "HUDCanvasPC/SafeArea/SlotsContainer/SkillButton1/ButtonGraphic/Frame", "hud", Role.Skill);
foreach (var name in new[] { "SkillButton_L1", "SkillButton_L2", "SkillButton_HoverFrame", "SkillButton_Highlight", "SkillButton_BG", "Slot", "wrriwd1" })
    Binding(name, "HUDCanvasPC/SafeArea/SlotsContainer/SkillButton1/ButtonGraphic/Frame", "hud", Role.None);
Binding("InputFieldFloatingBG", "UIChat/UIChatBody/InputField (TMP)", "chat", Role.ChatInput);
Binding("mainbox", "UIChat", "chat", Role.None);
Binding("Background", "UIChat/UIChatBody/Scroll View THISTHING", "chat", Role.None);
Binding("BarFillBackgroundSmall", "HUDCanvasPC/SafeArea/XPBars/XPBarSlider/Background", "hud", Role.Bar);
Binding("XPBarFill", "HUDCanvasPC/SafeArea/XPBars/XPBarSlider/Fill", "hud", Role.None);
Binding("ClassRankBarFill", "HUDCanvasPC/SafeArea/XPBars/RankXPBarSlider/Fill", "hud", Role.None);
Binding("9 Slice Main Window Background Round", "Image", "window", Role.Window);
Binding("9 Slice Main Window Background Round", "GameObject/WindowContainer/WindowBG", "window", Role.Window);
Binding("9 Slice Main Window Background Round", "GameObject/Items/Inventory_VerticalTabs/VerticalTabs/Image_VTabBG/GameObject/Image (5)", "window", Role.None);
Binding("9 Slice Main Window Background Round", "GameObject/WindowContainer/Tabs/General Options/ItemList/Viewport/Content/DropDown_FPS/Dropdown/Template", "window", Role.None);
Binding("Window Corner Bottom Left", "Image (2)", "window", Role.Ornament);
Binding("Window Corner Bottom Left", "GameObject/WindowContainer/Image (3)", "window", Role.Ornament);
Binding("9 Slice Tab", "GameObject/WindowContainer/Header", "window", Role.Ornament);
Binding("9 Slice Tab", "InventoryTypeTabs/Tabs/TabButton_All", "window", Role.None);
Binding("RarityIconBorder", "ItemIcon/BG/Border", "window", Role.None);
Binding("Close Button Normal", "CloseButton", "window", Role.None);
foreach (var name in new[] { "Portrait_Tier3", "Plate_Tier3", "Background_Default", "Lvlcircle_Default" })
    Binding(name, "HUDCanvasPC/SafeArea/PlayerPanel/PortraitLayer/Frame", "hud", Role.None);
Binding("MiniMenuBG_0", "MiniMenu/Button Options/Graphics/BG", "unrelated", Role.None);

using var native = JsonDocument.Parse(File.ReadAllText(Path.Combine(repo, "outputs/ui-themes-20261007/native-reference/sprite-metadata.json")));
var menu = native.RootElement.GetProperty("MiniMenuBG_0");
Assert(menu.GetProperty("rect").GetProperty("width").GetDouble() == 190, "Native menu canvas changed");
Assert(Math.Abs(menu.GetProperty("texture_offset").GetProperty("x").GetDouble() - 5.07612) < 0.0001, "Native trim changed");
var background = native.RootElement.GetProperty("9 Slice Main Window Background Round");
Assert(background.GetProperty("rect").GetProperty("width").GetDouble() == 449, "Window canvas changed");
foreach (var axis in new[] { "x", "y", "z", "w" }) Assert(background.GetProperty("border").GetProperty(axis).GetDouble() == 125, "Window slice changed");

using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(repo, "mod/InfinityLoader/Assets/UIThemes/art-manifest.json")));
var expected = new Dictionary<string, string>();
foreach (var art in manifest.RootElement.EnumerateArray())
{
    var key = art.GetProperty("theme").GetString()!;
    var asset = art.GetProperty("asset").GetString()!;
    var filename = Path.Combine(repo, "mod", "InfinityLoader", "Assets", "UIThemes", key, asset + ".png");
    var hash = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(filename))).ToLowerInvariant();
    Assert(hash == art.GetProperty("sha256").GetString(), "Artwork hash changed: " + filename);
    Assert(art.GetProperty("corner_alpha").GetInt32() == 0, "Nontransparent outside corner");
    Assert(asset != "skill" || art.GetProperty("center_alpha").GetInt32() == 0, "Skill center must be transparent");
    expected.Add($"InfinityLoader.UIThemes.{key}.{asset}.png", hash);
}
Assert(expected.Count == 9, "Need three complete theme sets");
var dll = Path.Combine(repo, "mod/InfinityLoader/bin/Release/InfinityLoader.dll");
using (var file = File.OpenRead(dll))
using (var pe = new PEReader(file))
{
    var metadata = pe.GetMetadataReader();
    var found = new HashSet<string>();
    foreach (var handle in metadata.ManifestResources)
    {
        var resource = metadata.GetManifestResource(handle);
        var name = metadata.GetString(resource.Name);
        if (!expected.TryGetValue(name, out var hash)) continue;
        Assert(resource.Implementation.IsNil, "Artwork should be embedded");
        var reader = pe.GetSectionData(pe.PEHeaders.CorHeader!.ResourcesDirectory.RelativeVirtualAddress).GetReader((int)resource.Offset, pe.PEHeaders.CorHeader.ResourcesDirectory.Size - (int)resource.Offset);
        int length = reader.ReadInt32();
        Assert(Convert.ToHexString(SHA256.HashData(reader.ReadBytes(length))).ToLowerInvariant() == hash, "Embedded resource mismatch: " + name);
        found.Add(name);
    }
    Assert(found.SetEquals(expected.Keys), "Embedded theme resource names do not match runtime lookups");
}
var game = Environment.GetEnvironmentVariable("AQWI_GAME_DIR") ?? @"C:\Program Files (x86)\Steam\steamapps\common\AdventureQuest Worlds Unity Playtest";
var managed = Path.Combine(game, "AdventureQuest Worlds Infinity_Data", "Managed");
void Hook(string assembly, string type, string method)
{
    using var file = File.OpenRead(Path.Combine(managed, assembly));
    using var pe = new PEReader(file);
    var md = pe.GetMetadataReader();
    bool exists = md.TypeDefinitions.Select(md.GetTypeDefinition).Any(t => md.GetString(t.Name) == type && t.GetMethods().Select(md.GetMethodDefinition).Any(m => md.GetString(m.Name) == method));
    Assert(exists, "Native hook missing: " + type + "." + method);
}
Hook("Assembly-CSharp.dll", "HUDCanvas", "Start");
Hook("Assembly-CSharp.dll", "UIOptionsManager", "Awake");
Hook("Assembly-CSharp.dll", "UIOptionsManager", "DoResetToDefaults");
Hook("UnityEngine.UI.dll", "Image", "OnEnable");
Console.WriteLine($"PASS: {checks} native binding, exclusion, metadata, asset, embedded resource and hook checks.");
Console.WriteLine("Unity gameplay/rendering and Default restoration still require client runtime verification.");
