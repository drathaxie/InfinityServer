using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

// Some player installations fail to bind EulerToQuaternion's injected call.
// Both Rotator.Update and PreviewSpriteImage.setup use this shared conversion.
// Probe the native binding first; working installations keep their native path.
internal static class UnityRotationCompatibility
{
    internal static void Install(Harmony harmony, Action<string> log, bool forceForValidation = false)
    {
        var method = AccessTools.DeclaredMethod(typeof(Quaternion), "Internal_FromEulerRad",
            new[] { typeof(Vector3).MakeByRefType() });
        if (method == null || method.GetMethodBody() == null)
            throw new MissingMethodException("Quaternion Euler managed wrapper changed");
        bool needsRepair = forceForValidation;
        try
        {
            method.Invoke(null, new object[] { new Vector3(.1f, .2f, .3f) });
        }
        catch (TargetInvocationException error) when (error.InnerException is MissingMethodException)
        {
            needsRepair = true;
            log("[unity-rotation] native Euler binding failed: " + error.InnerException.Message +
                "; core=" + typeof(Quaternion).Assembly.Location +
                "; mvid=" + typeof(Quaternion).Module.ModuleVersionId);
        }
        if (!needsRepair)
        {
            log("[unity-rotation] native Euler binding passed");
            return;
        }
        harmony.Patch(method, prefix: new HarmonyMethod(typeof(UnityRotationCompatibility), nameof(FromEulerPrefix)));
        var actual = (Quaternion)method.Invoke(null, new object[] { new Vector3(.1f, .2f, .3f) });
        var expected = FromRadians(new Vector3(.1f, .2f, .3f));
        if (Math.Abs(actual.x - expected.x) + Math.Abs(actual.y - expected.y) +
            Math.Abs(actual.z - expected.z) + Math.Abs(actual.w - expected.w) > 1e-6)
            throw new InvalidOperationException("Euler binding repair did not take effect");
        log("[unity-rotation] managed Euler repair installed and verified");
    }

    private static bool FromEulerPrefix(ref Vector3 __0, ref Quaternion __result)
    {
        __result = FromRadians(__0);
        return false;
    }

    internal static Quaternion FromRadians(Vector3 radians)
    {
        // Unity's Z-X-Y order: q = qY * qX * qZ. Pure managed arithmetic avoids
        // calling the same failing injected rotation helper through another API.
        double sx = Math.Sin(radians.x * .5), cx = Math.Cos(radians.x * .5);
        double sy = Math.Sin(radians.y * .5), cy = Math.Cos(radians.y * .5);
        double sz = Math.Sin(radians.z * .5), cz = Math.Cos(radians.z * .5);
        return new Quaternion((float)(cy * sx * cz + sy * cx * sz),
            (float)(sy * cx * cz - cy * sx * sz), (float)(cy * cx * sz - sy * sx * cz),
            (float)(cy * cx * cz + sy * sx * sz));
    }
}
