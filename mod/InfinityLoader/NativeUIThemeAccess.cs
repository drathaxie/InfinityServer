#nullable enable
using System.Collections.Generic;

// Achievement requirements are evaluated from this character's server-supplied
// PlayerInfo. Future quest rewards can grant a bit through the same achievement path.
internal static class NativeUIThemeAccess
{
    internal const string AchievementField = "cosmetics26";
    internal const int AchievementBit = 0;
    internal const string AchievementName = "Border Collector";
    internal const string AchievementDescription = "Redeem the border promo code to unlock DoomKnight, Chaos, and BLOD UI themes.";
    internal const string PreferencePrefix = "InfinityLoader.UITheme.v2.";

    internal static bool IsUnlocked(int index, IDictionary<string, int>? achievements)
    {
        if (index == 0) return true;
        if (index < 1 || index > 3 || achievements == null) return false;
        int bits;
        return achievements.TryGetValue(AchievementField, out bits) && (bits & (1 << AchievementBit)) != 0;
    }

    internal static int ResolveSelection(int savedIndex, IDictionary<string, int>? achievements)
    {
        return IsUnlocked(savedIndex, achievements) ? savedIndex : 0;
    }

    internal static string? PreferenceKey(int characterId)
    {
        return characterId > 0 ? PreferencePrefix + characterId : null;
    }
}
