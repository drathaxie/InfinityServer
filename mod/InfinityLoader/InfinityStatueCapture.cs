using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Threading;
using UnityEngine;

// Captures the player's fully assembled Unity avatar, converts it to neutral stone,
// adds the tiered pedestal used by generated statues, then uploads the transparent PNG.
public static class InfinityStatueCapture
{
    private const int Width = 1024;
    private const int Height = 1536;
    private const int AvatarFootTargetX = 465;
    private const int AvatarFootTargetY = 608;
    private const int AvatarTargetTopY = 1540;
    private const int AvatarTargetLeftX = 112;
    private const int AvatarTargetRightX = 1008;
    private const float PedestalScale = 0.90f;
    private const int PedestalPivotX = 512;
    private const int PedestalPivotY = 0;
    private const string PedestalResourceName = "InfinityLoader.StatuePedestal.png";
    private static int _busy;
    private static Color32[] _pedestalPixels;

    public static void Begin(string api)
    {
        PlayerInfo info = Entity.myPlayerData == null ? null : Entity.myPlayerData.Info;
        Account account = SessionState.LoginData == null ? null : SessionState.LoginData.account;
        if (info == null || account == null || info.CharID <= 0 || string.IsNullOrEmpty(account.sToken))
        {
            InfinityLoaderMod.SafeLog("[statue] capture skipped: no authenticated player");
            return;
        }
        if (Interlocked.CompareExchange(ref _busy, 1, 0) != 0) return;

        byte[] png = null;
        Texture2D texture = null;
        try
        {
            texture = CaptureAvatar();
            if (texture != null) png = texture.EncodeToPNG();
        }
        catch (Exception ex)
        {
            InfinityLoaderMod.SafeLog("[statue] render failed " + ex);
        }
        finally
        {
            if (texture != null) UnityEngine.Object.Destroy(texture);
        }

        if (png == null || png.Length < 64)
        {
            Interlocked.Exchange(ref _busy, 0);
            return;
        }

        int cid = info.CharID;
        string token = account.sToken;
        string url = api + "statue/upload";
        ThreadPool.QueueUserWorkItem(delegate
        {
            try
            {
                using (var web = new WebClient())
                {
                    web.Headers[HttpRequestHeader.ContentType] = "image/png";
                    web.Headers["ccid"] = cid.ToString();
                    web.Headers["token"] = token;
                    web.UploadData(url, "PUT", png);
                }
                InfinityLoaderMod.SafeLog("[statue] uploaded real avatar cid=" + cid
                    + " bytes=" + png.Length);
            }
            catch (Exception ex)
            {
                InfinityLoaderMod.SafeLog("[statue] upload failed " + ex.Message);
            }
            finally { Interlocked.Exchange(ref _busy, 0); }
        });
    }

    private static Texture2D CaptureAvatar()
    {
        GameObject root = Entity.mainPlayer == null ? null : Entity.mainPlayer.getGameObject();
        if (root == null) throw new InvalidOperationException("main player avatar is not spawned");

        var renderers = new List<SpriteRenderer>();
        bool haveBounds = false;
        Bounds bounds = default(Bounds);
        foreach (SpriteRenderer renderer in root.GetComponentsInChildren<SpriteRenderer>(true))
        {
            if (!IsCharacterRenderer(renderer)) continue;
            renderers.Add(renderer);
            if (!haveBounds)
            {
                bounds = renderer.bounds;
                haveBounds = true;
            }
            else bounds.Encapsulate(renderer.bounds);
        }
        if (!haveBounds || bounds.size.x < 0.001f || bounds.size.y < 0.001f)
            throw new InvalidOperationException("assembled avatar has no visible sprites");

        int captureLayer = LayerMask.NameToLayer("RenderTextures");
        if (captureLayer < 0) captureLayer = 31;
        var oldLayers = new Dictionary<GameObject, int>();
        foreach (SpriteRenderer renderer in renderers)
        {
            GameObject go = renderer.gameObject;
            if (!oldLayers.ContainsKey(go)) oldLayers.Add(go, go.layer);
            go.layer = captureLayer;
        }

        GameObject cameraObject = null;
        Camera camera = null;
        RenderTexture target = null;
        RenderTexture previous = RenderTexture.active;
        Texture2D output = null;
        try
        {
            cameraObject = new GameObject("InfinityStatueCaptureCamera");
            cameraObject.hideFlags = HideFlags.HideAndDontSave;
            camera = cameraObject.AddComponent<Camera>();
            camera.enabled = false;
            camera.orthographic = true;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = Color.clear;
            camera.cullingMask = 1 << captureLayer;
            camera.nearClipPlane = 0.01f;
            camera.farClipPlane = 500f;
            camera.allowHDR = false;
            camera.allowMSAA = true;
            camera.aspect = (float)Width / Height;

            // Capture generously first, then pixel-fit the actual rendered avatar.
            float byHeight = bounds.size.y / (2f * 0.62f);
            float byWidth = bounds.size.x / (2f * camera.aspect * 0.54f);
            camera.orthographicSize = Mathf.Max(byHeight, byWidth) * 1.08f;
            float worldPerPixel = 2f * camera.orthographicSize / Height;
            float desiredCenterOffset = 150f * worldPerPixel;
            cameraObject.transform.position = new Vector3(
                bounds.center.x, bounds.center.y - desiredCenterOffset, bounds.min.z - 100f);

            target = new RenderTexture(Width, Height, 24, RenderTextureFormat.ARGB32,
                                       RenderTextureReadWrite.sRGB);
            target.antiAliasing = 4;
            target.filterMode = FilterMode.Bilinear;
            target.Create();
            camera.targetTexture = target;
            camera.Render();
            Vector3 footViewport = camera.WorldToViewportPoint(root.transform.position);
            int footPixelX = Mathf.RoundToInt(footViewport.x * Width);
            int footPixelY = Mathf.RoundToInt(footViewport.y * Height);

            RenderTexture.active = target;
            output = new Texture2D(Width, Height, TextureFormat.RGBA32, false, false);
            output.ReadPixels(new Rect(0, 0, Width, Height), 0, 0, false);
            output.Apply(false, false);

            Color32[] pixels = output.GetPixels32();
            GradeAvatarAsStone(pixels);
            Color32[] avatar = FitAvatarToPedestal(pixels, footPixelX, footPixelY);
            CompositeStatue(pixels, avatar);
            output.SetPixels32(pixels);
            output.Apply(false, false);
            return output;
        }
        catch
        {
            if (output != null) UnityEngine.Object.Destroy(output);
            throw;
        }
        finally
        {
            RenderTexture.active = previous;
            if (camera != null) camera.targetTexture = null;
            if (target != null)
            {
                target.Release();
                UnityEngine.Object.Destroy(target);
            }
            if (cameraObject != null) UnityEngine.Object.Destroy(cameraObject);
            foreach (KeyValuePair<GameObject, int> pair in oldLayers)
                if (pair.Key != null) pair.Key.layer = pair.Value;
        }
    }

    private static bool IsCharacterRenderer(SpriteRenderer renderer)
    {
        if (renderer == null || !renderer.enabled || renderer.sprite == null
            || !renderer.gameObject.activeInHierarchy) return false;
        string name = (renderer.gameObject.name ?? "").ToLowerInvariant();
        return name != "footprintring" && name != "shadow"
            && !name.StartsWith("nameplate") && !name.Contains("targetring");
    }

    private static void GradeAvatarAsStone(Color32[] pixels)
    {
        for (int i = 0; i < pixels.Length; i++)
        {
            Color32 p = pixels[i];
            if (p.a < 8)
            {
                pixels[i] = new Color32(0, 0, 0, 0);
                continue;
            }
            float luminance = p.r * 0.2126f + p.g * 0.7152f + p.b * 0.0722f;
            byte stone = (byte)Mathf.Clamp(18f + luminance * 0.68f, 18f, 194f);
            pixels[i] = new Color32(stone, stone, stone, p.a);
        }
    }

    private static Color32[] FitAvatarToPedestal(Color32[] pixels, int footPixelX, int footPixelY)
    {
        int minX = Width;
        int minY = Height;
        int maxX = -1;
        int maxY = -1;
        for (int y = 0; y < Height; y++)
        {
            int row = y * Width;
            for (int x = 0; x < Width; x++)
            {
                if (pixels[row + x].a < 8) continue;
                if (x < minX) minX = x;
                if (x > maxX) maxX = x;
                if (y < minY) minY = y;
                if (y > maxY) maxY = y;
            }
        }
        if (maxX < minX || maxY < minY) return new Color32[pixels.Length];

        int srcW = maxX - minX + 1;
        int srcH = maxY - minY + 1;
        int targetW = AvatarTargetRightX - AvatarTargetLeftX + 1;
        int targetH = AvatarTargetTopY - AvatarFootTargetY + 1;
        int localFootX = Mathf.Clamp(footPixelX, minX, maxX) - minX;
        int localFootY = Mathf.Clamp(footPixelY, minY, maxY) - minY;
        int rightOfFoot = Mathf.Max(1, srcW - localFootX - 1);
        int leftOfFoot = Mathf.Max(1, localFootX);
        float canvasScale = Mathf.Min(
            (AvatarFootTargetX - 1f) / leftOfFoot,
            (Width - 1f - AvatarFootTargetX) / rightOfFoot);
        float scale = Mathf.Min((float)targetW / srcW, (float)targetH / srcH, canvasScale);
        int dstW = Mathf.Max(1, Mathf.RoundToInt(srcW * scale));
        int dstH = Mathf.Max(1, Mathf.RoundToInt(srcH * scale));
        int dstX = AvatarFootTargetX - Mathf.RoundToInt(localFootX * scale);
        int dstY = AvatarFootTargetY - Mathf.RoundToInt(localFootY * scale);

        var fitted = new Color32[pixels.Length];
        for (int y = 0; y < dstH; y++)
        {
            float srcY = minY + (y + 0.5f) / scale - 0.5f;
            int outY = dstY + y;
            if (outY < 0 || outY >= Height) continue;
            for (int x = 0; x < dstW; x++)
            {
                float srcX = minX + (x + 0.5f) / scale - 0.5f;
                int outX = dstX + x;
                if (outX < 0 || outX >= Width) continue;
                fitted[outY * Width + outX] = SampleBilinear(pixels, srcX, srcY);
            }
        }
        InfinityLoaderMod.SafeLog("[statue] fitted avatar src=" + srcW + "x" + srcH
            + " dst=" + dstW + "x" + dstH + " at " + dstX + "," + dstY
            + " foot=" + footPixelX + "," + footPixelY
            + " localFoot=" + localFootX + "," + localFootY
            + " targetFoot=" + AvatarFootTargetX + "," + AvatarFootTargetY
            + " canvasScale=" + canvasScale.ToString("0.###")
            + " scale=" + scale.ToString("0.###"));
        return fitted;
    }

    private static Color32 SampleBilinear(Color32[] pixels, float x, float y)
    {
        x = Mathf.Clamp(x, 0f, Width - 1f);
        y = Mathf.Clamp(y, 0f, Height - 1f);
        int x0 = Mathf.FloorToInt(x);
        int y0 = Mathf.FloorToInt(y);
        int x1 = Mathf.Min(Width - 1, x0 + 1);
        int y1 = Mathf.Min(Height - 1, y0 + 1);
        float tx = x - x0;
        float ty = y - y0;
        Color32 a = pixels[y0 * Width + x0];
        Color32 b = pixels[y0 * Width + x1];
        Color32 c = pixels[y1 * Width + x0];
        Color32 d = pixels[y1 * Width + x1];
        return new Color32(
            (byte)Mathf.RoundToInt(Mathf.Lerp(Mathf.Lerp(a.r, b.r, tx), Mathf.Lerp(c.r, d.r, tx), ty)),
            (byte)Mathf.RoundToInt(Mathf.Lerp(Mathf.Lerp(a.g, b.g, tx), Mathf.Lerp(c.g, d.g, tx), ty)),
            (byte)Mathf.RoundToInt(Mathf.Lerp(Mathf.Lerp(a.b, b.b, tx), Mathf.Lerp(c.b, d.b, tx), ty)),
            (byte)Mathf.RoundToInt(Mathf.Lerp(Mathf.Lerp(a.a, b.a, tx), Mathf.Lerp(c.a, d.a, tx), ty)));
    }

    private static void CompositeStatue(Color32[] pixels, Color32[] avatar)
    {
        Color32[] pedestal = LoadPedestalPixels();
        if (pedestal == null || pedestal.Length != pixels.Length) return;
        for (int i = 0; i < pixels.Length; i++)
        {
            Color32 ped = SamplePedestal(pedestal, i);
            Color32 av = avatar[i];
            Color32 outColor = ped;
            if (av.a > 0)
            {
                if (av.a == 255) outColor = av;
                else
                {
                    float a = av.a / 255f;
                    outColor = new Color32(
                        (byte)Mathf.Clamp(Mathf.RoundToInt(av.r * a + ped.r * (1f - a)), 0, 255),
                        (byte)Mathf.Clamp(Mathf.RoundToInt(av.g * a + ped.g * (1f - a)), 0, 255),
                        (byte)Mathf.Clamp(Mathf.RoundToInt(av.b * a + ped.b * (1f - a)), 0, 255),
                        (byte)Mathf.Clamp(Mathf.RoundToInt(av.a + ped.a * (1f - a)), 0, 255));
                }
            }
            pixels[i] = outColor;
        }
    }

    private static Color32 SamplePedestal(Color32[] pedestal, int index)
    {
        int x = index % Width;
        int y = index / Width;
        float srcX = PedestalPivotX + (x - PedestalPivotX) / PedestalScale;
        float srcY = PedestalPivotY + (y - PedestalPivotY) / PedestalScale;
        if (srcX < 0f || srcX > Width - 1f || srcY < 0f || srcY > Height - 1f)
            return new Color32(0, 0, 0, 0);
        return SampleBilinear(pedestal, srcX, srcY);
    }
    private static Color32[] LoadPedestalPixels()
    {
        if (_pedestalPixels != null) return _pedestalPixels;
        using (Stream stream = typeof(InfinityStatueCapture).Assembly
                   .GetManifestResourceStream(PedestalResourceName))
        {
            if (stream == null)
            {
                InfinityLoaderMod.SafeLog("[statue] missing pedestal resource");
                return null;
            }
            var png = new byte[stream.Length];
            int read = stream.Read(png, 0, png.Length);
            if (read != png.Length) return null;
            var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false, false);
            try
            {
                if (!tex.LoadImage(png, false) || tex.width != Width || tex.height != Height)
                {
                    InfinityLoaderMod.SafeLog("[statue] invalid pedestal dimensions");
                    return null;
                }
                _pedestalPixels = tex.GetPixels32();
                return _pedestalPixels;
            }
            finally { UnityEngine.Object.Destroy(tex); }
        }
    }
}
