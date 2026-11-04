using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.Rendering;

public static class MapPrototypeBuilder
{
    [Serializable] public class Point { public float x,y; }
    [Serializable] public class Polygon { public Point[] points; }
    [Serializable] public class Spawn { public string name; public float x,y; }
    [Serializable] public class Exit { public string name,targetCell,targetPad; public Polygon[] polygons; }
    [Serializable] public class Cell { public string name,image; public float sourceScale,sourceSpeed; public Spawn[] spawns; public Polygon[] walkables,blockers; public Exit[] exits; }
    [Serializable] public class TileDef { public int page,x,y,w,h,ox,oy; }
    [Serializable] public class FrameDef { public string name; public int page,x,y,w,h; public TileDef[] tiles; }
    [Serializable] public class AtlasDef { public string path; public int height; }
    [Serializable] public class VisualLayer { public string name,group; public int order,period,source_char; public int[] frames; public float x,y; }
    [Serializable] public class VisualGroup { public string name,kind; public float speed,x,y,phase; }
    [Serializable] public class Effect { public string kind,group,after,mask; public int depth,order; public float x0,x1,y0,y1,x,y,size,density; }
    [Serializable] public class EffectSource { public string name,path; }
    [Serializable] public class VisualCell { public string name; public VisualLayer[] layers; public VisualGroup[] groups; public Effect[] effects; }
    [Serializable] public class InputData { public float width,height,pixelsPerUnit,fps,cameraZoom,rasterScale; public string outputDir,atlas; public Cell[] cells; public bool layered,gpuCompression; public int atlasHeight; public AtlasDef[] visualAtlases; public FrameDef[] visualFrames; public VisualCell[] visualCells; public int[] sampleFrames; public string effectsBundle,effectArtDir,batSheet; public EffectSource[] effectSources; public int batCols,batRows,batFrames; }
    [Serializable] public class CheckRecord { public string cell,kind,target; public int samples; public bool passed; }
    [Serializable] public class Report { public string status,proofBoundary; public int cells; public CheckRecord[] checks; }
    static InputData input;
    static List<CheckRecord> checks = new List<CheckRecord>();
    class VisualClock { public GameObject go; public AnimationClip clip; public int period; }
    static Dictionary<string,List<VisualClock>> clocks=new Dictionary<string,List<VisualClock>>();
    static Sprite[] visualSprites;
    static Sprite[][] tileSprites;
    // Native effects replacing Flash art: the Skeletal Fire Mage's torch-style fire (npcs/46573, live AE) and ground smoke.
    const string FireSource="Animation/FX_Fire Med_wGlow/ps_fire med";
    static Dictionary<string,AssetBundle> effectBundles=new Dictionary<string,AssetBundle>();
    static Dictionary<string,List<(GameObject node,string kind)>> effectNodes=new Dictionary<string,List<(GameObject node,string kind)>>();
    static Dictionary<string,Material> effectMaterials=new Dictionary<string,Material>();
    static List<string> skippedEffectParts=new List<string>();
    static Dictionary<string,(AnimationClip clip,int banners)> swayClips=new Dictionary<string,(AnimationClip clip,int banners)>();
    static Vector2 Pos(float x,float y) => new Vector2(x/input.pixelsPerUnit,-y/input.pixelsPerUnit);

    static GameObject Node(string name,Transform parent) { var go=new GameObject(name); go.transform.SetParent(parent,false); return go; }
    static PolygonCollider2D PolygonObject(string name,Transform parent,Polygon polygon)
    {
        var collider=Node(name,parent).AddComponent<PolygonCollider2D>();
        collider.points=polygon.points.Select(p=>Pos(p.x,p.y)).ToArray();
        return collider;
    }
    static void Layers()
    {
        var tags=new SerializedObject(AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/TagManager.asset")[0]);
        var layers=tags.FindProperty("layers");
        // Walkable/Blocker IDs are confirmed by the inspected native bundles.
        layers.GetArrayElementAtIndex(21).stringValue="Walkable";
        layers.GetArrayElementAtIndex(20).stringValue="Blocker";
        layers.GetArrayElementAtIndex(10).stringValue="Level";
        tags.ApplyModifiedProperties();
        // IDs read from the installed client's globalgamemanagers TagManager.
        var sorting=tags.FindProperty("m_SortingLayers");sorting.arraySize=2;
        sorting.GetArrayElementAtIndex(0).FindPropertyRelative("name").stringValue="BGFront";
        sorting.GetArrayElementAtIndex(0).FindPropertyRelative("uniqueID").intValue=1256558175;
        sorting.GetArrayElementAtIndex(1).FindPropertyRelative("name").stringValue="Default";
        sorting.GetArrayElementAtIndex(1).FindPropertyRelative("uniqueID").intValue=0;
        tags.ApplyModifiedProperties();
    }
    static void Capture(Camera camera,string name)
    {
        SortingGroup.UpdateAllSortingGroups();
        float density=Mathf.Max(1,input.rasterScale);
        var rt=new RenderTexture((int)(input.width*density),(int)(input.height*density),0,RenderTextureFormat.ARGB32);
        camera.targetTexture=rt; camera.Render(); RenderTexture.active=rt;
        var image=new Texture2D(rt.width,rt.height,TextureFormat.RGBA32,false);
        image.ReadPixels(new Rect(0,0,rt.width,rt.height),0,0); image.Apply();
        File.WriteAllBytes(Path.Combine(input.outputDir,name+"-unity.png"),image.EncodeToPNG());
        camera.targetTexture=null; RenderTexture.active=null;
        UnityEngine.Object.DestroyImmediate(image); UnityEngine.Object.DestroyImmediate(rt);
    }
    static void Check(bool passed,string cell,string kind,string target,int samples=1)
    {
        checks.Add(new CheckRecord { passed=passed,cell=cell,kind=kind,target=target,samples=samples });
        if(!passed) throw new Exception("MAP_PROTOTYPE_CHECK_FAILED "+cell+" "+kind+" "+target);
    }
    static Texture2D ReadFrame(Camera camera)
    {
        SortingGroup.UpdateAllSortingGroups();
        var rt=new RenderTexture((int)input.width,(int)input.height,0,RenderTextureFormat.ARGB32);
        camera.targetTexture=rt;camera.Render();RenderTexture.active=rt;
        var image=new Texture2D(rt.width,rt.height,TextureFormat.RGBA32,false);
        image.ReadPixels(new Rect(0,0,rt.width,rt.height),0,0);image.Apply();
        camera.targetTexture=null;RenderTexture.active=null;UnityEngine.Object.DestroyImmediate(rt);
        return image;
    }
    static void VerifyDepthAndParallax(GameObject instance,Camera camera)
    {
        var tex=new Texture2D(1,1,TextureFormat.RGBA32,false);tex.SetPixel(0,0,Color.magenta);tex.Apply();
        var sprite=Sprite.Create(tex,new Rect(0,0,1,1),new Vector2(.5f,0),1);
        foreach(var spec in input.visualCells.Where(c=>c.groups!=null&&c.groups.Length>0))
        {
            foreach(Transform child in instance.transform)child.gameObject.SetActive(child.name==spec.name);
            var cell=instance.transform.Find(spec.name);var bg=cell.GetComponentInChildren<BGParallax>();
            var props=cell.GetComponentsInChildren<ZOffset>();
            var all=cell.GetComponentsInChildren<SpriteRenderer>();
            var enabled=all.Select(r=>r.enabled).ToArray();
            if(spec.name=="Enter"&&spec.groups.Any(g=>g.name=="DragonBackdrop"))
            {
                var dragon=cell.GetComponentsInChildren<Transform>().Single(t=>t.name=="DragonBackdrop");
                var dragonArt=dragon.GetComponentsInChildren<SpriteRenderer>();
                Check(dragon.GetComponent<ZOffset>()==null&&dragonArt.All(r=>r.sortingLayerName=="BGFront"),spec.name,"dragon_background_binding","DragonBackdrop");
                int totalOverlap=0;
                bool hasActors=spec.groups.Any(g=>g.kind=="actor");
                foreach(var group in spec.groups.Where(g=>g.kind=="actor"))
                {
                    var actor=props.Single(p=>p.name==group.name);var actorArt=actor.GetComponentsInChildren<SpriteRenderer>();
                    Check(actor.sortingGroup.sortingLayerName=="Default",spec.name,"npc_depth_binding",group.name);
                    foreach(var r in all)r.enabled=actorArt.Contains(r);
                    camera.backgroundColor=Color.clear;
                    var mask=ReadFrame(camera);var a=mask.GetPixels32();
                    foreach(var r in all)r.enabled=dragonArt.Contains(r);
                    var dragonMask=ReadFrame(camera);var d=dragonMask.GetPixels32();
                    foreach(var r in all)r.enabled=actorArt.Contains(r)||dragonArt.Contains(r);
                    var combined=ReadFrame(camera);var b=combined.GetPixels32();
                    int overlap=0,wrong=0;
                    for(int i=0;i<a.Length;i++)if(a[i].a==255&&d[i].a>250)
                    {
                        overlap++;
                        if(Mathf.Abs(a[i].r-b[i].r)>2||Mathf.Abs(a[i].g-b[i].g)>2||Mathf.Abs(a[i].b-b[i].b)>2)wrong++;
                    }
                    Check(wrong==0,spec.name,"npc_dragon_opaque_pixels",group.name+" overlap="+overlap+" wrong="+wrong);
                    totalOverlap+=overlap;
                    File.WriteAllBytes(Path.Combine(input.outputDir,spec.name+"-"+group.name+"-dragon-composition.png"),combined.EncodeToPNG());
                    UnityEngine.Object.DestroyImmediate(mask);UnityEngine.Object.DestroyImmediate(dragonMask);UnityEngine.Object.DestroyImmediate(combined);
                }
                if(hasActors)Check(totalOverlap>50,spec.name,"npc_dragon_overlap_exercised",totalOverlap.ToString());
                for(int i=0;i<all.Length;i++)all[i].enabled=enabled[i];
                camera.backgroundColor=Color.black;
                int nativeOrder=0;foreach(var p in props.OrderByDescending(p=>p.transform.position.y-p.yOffset))p.sortingGroup.sortingOrder=(++nativeOrder)*500;
                Capture(camera,spec.name+"-npc-order");
            }
            var probe=Node("DepthQA_NotIncludedInBundle",cell);var sr=probe.AddComponent<SpriteRenderer>();sr.sprite=sprite;
            probe.transform.localScale=new Vector3(22/input.pixelsPerUnit,70/input.pixelsPerUnit,1);
            var probeSort=probe.AddComponent<SortingGroup>();probeSort.sortingLayerName="Default";
            foreach(var prop in props)
            {
                sr.enabled=false;
                foreach(var r in all)r.enabled=r.GetComponentInParent<ZOffset>()==prop;
                camera.backgroundColor=Color.clear;
                var mask=ReadFrame(camera);var pixels=mask.GetPixels32();
                float sourceY=-prop.transform.position.y*input.pixelsPerUnit;
                int bestX=0,best=0;
                for(int x=0;x<mask.width;x++)
                {
                    int score=0;
                    for(int y=Mathf.Max(0,(int)sourceY-60);y<Mathf.Min(mask.height,(int)sourceY-6);y++)
                        if(pixels[(mask.height-1-y)*mask.width+x].a>240)score++;
                    if(score>best){best=score;bestX=x;}
                }
                UnityEngine.Object.DestroyImmediate(mask);
                Check(best>0,spec.name,"depth_silhouette",prop.name);
                for(int i=0;i<all.Length;i++)all[i].enabled=enabled[i]&&(all[i].GetComponentInParent<ZOffset>()==null||all[i].GetComponentInParent<ZOffset>()==prop);
                camera.backgroundColor=Color.black;sr.enabled=true;
                int behind=0,front=0;
                foreach(bool isFront in new[]{false,true})
                {
                    probe.transform.position=new Vector3((bestX+.5f)/input.pixelsPerUnit,-(sourceY+(isFront?6:-6))/input.pixelsPerUnit,0);
                    bool playerIsLower=probe.transform.position.y<prop.transform.position.y;
                    probeSort.sortingOrder=playerIsLower?1000:500;
                    prop.sortingGroup.sortingOrder=playerIsLower?500:1000;
                    var frame=ReadFrame(camera);int count=frame.GetPixels32().Count(p=>p.r>250&&p.g<2&&p.b>250);
                    File.WriteAllBytes(Path.Combine(input.outputDir,spec.name+"-"+prop.name+"-"+(isFront?"front":"behind")+".png"),frame.EncodeToPNG());
                    UnityEngine.Object.DestroyImmediate(frame);
                    if(isFront)front=count;else behind=count;
                }
                Check(front>behind,spec.name,"depth_front_behind",prop.name+" visible="+behind+"/"+front);
                prop.sortingGroup.sortingOrder=0;
            }
            UnityEngine.Object.DestroyImmediate(probe);
            for(int i=0;i<all.Length;i++)all[i].enabled=enabled[i];
            int order=0;foreach(var p in props.OrderByDescending(p=>p.transform.position.y-p.yOffset))p.sortingGroup.sortingOrder=(++order)*500;
            var initial=bg.levels.Select(l=>l.Layer.transform.position).ToArray();
            var confiner=cell.GetComponent<MapCell>().ConfinerCollider;
            // Camera travel and parallax at a 16:9 screen. With the whole room height framed (Flash-stage framing,
            // calibration v9) the view fits inside the room and still pans a little; v3-v8's crop panned >20 units.
            float captureAspect=camera.aspect;camera.aspect=16f/9;
            float halfWidth=input.cameraZoom*camera.aspect;
            float left=confiner.bounds.min.x+halfWidth,right=confiner.bounds.max.x-halfWidth;
            Check(right-left>1&&input.cameraZoom*2<=confiner.bounds.size.y+.01f,spec.name,"camera_horizontal_travel",(right-left).ToString());
            foreach(bool atRight in new[]{false,true})
            {
                camera.orthographicSize=input.cameraZoom;
                camera.transform.position=new Vector3(atRight?right:left,confiner.bounds.min.y+input.cameraZoom,-10);
                var delta=camera.transform.position-bg.transform.position;
                for(int i=0;i<bg.levels.Length;i++)
                {
                    var level=bg.levels[i];
                    var offset=i==0?Vector3.zero:new Vector3(delta.x*level.Speed/100,-delta.y*level.Speed/100,0);
                    level.Layer.transform.position=initial[i]+offset;
                    Check(i!=0||level.Layer.transform.position==initial[i],spec.name,"parallax_ground_fixed",level.Layer.name);
                    if(i>0)Check(Mathf.Abs(offset.x)>0.1f,spec.name,"parallax_relative_motion",level.Layer.name);
                }
                Capture(camera,spec.name+"-camera-"+(atRight?"right":"left"));
            }
            for(int i=0;i<bg.levels.Length;i++)bg.levels[i].Layer.transform.position=initial[i];
            camera.aspect=captureAspect;
            camera.orthographicSize=input.height/input.pixelsPerUnit/2;
            camera.transform.position=new Vector3(input.width/input.pixelsPerUnit/2,-input.height/input.pixelsPerUnit/2,-10);
        }
        UnityEngine.Object.DestroyImmediate(sprite);UnityEngine.Object.DestroyImmediate(tex);
    }
    static void PrepareVisualSprites()
    {
        Directory.CreateDirectory("Assets/Generated/Visuals");
        var pages=input.visualAtlases??new[]{new AtlasDef{path=input.atlas,height=input.atlasHeight}};
        var textures=new List<Texture2D>();
        foreach(var page in pages)
        {
            var importer=(TextureImporter)AssetImporter.GetAtPath(page.path);
            importer.textureType=TextureImporterType.Default; importer.textureCompression=TextureImporterCompression.Uncompressed;
            importer.mipmapEnabled=false; importer.isReadable=false;
            importer.filterMode=input.rasterScale>1?FilterMode.Bilinear:FilterMode.Point;
            importer.wrapMode=TextureWrapMode.Clamp; importer.npotScale=TextureImporterNPOTScale.None;
            // Pages are laid out in 12-texel blocks (map_visuals.BLOCK), so BC7 4x4 and ASTC 6x6 blocks never mix two images.
            foreach(var (platform,format) in new[]{("Standalone",TextureImporterFormat.BC7),("Android",TextureImporterFormat.ASTC_6x6)})
            {
                var settings=importer.GetPlatformTextureSettings(platform);
                settings.overridden=input.gpuCompression; settings.format=format; settings.maxTextureSize=4096; settings.compressionQuality=100;
                importer.SetPlatformTextureSettings(settings);
            }
            importer.alphaIsTransparency=false; importer.maxTextureSize=4096; importer.SaveAndReimport();
            textures.Add(AssetDatabase.LoadAssetAtPath<Texture2D>(page.path));
        }
        float ppu=input.pixelsPerUnit*Mathf.Max(1,input.rasterScale);
        Sprite Make(string name,int page,int x,int y,int w,int h)
        {
            var sprite=Sprite.Create(textures[page],new Rect(x,pages[page].height-y-h,w,h),new Vector2(.5f,.5f),ppu,0,SpriteMeshType.FullRect);
            sprite.name=name; AssetDatabase.CreateAsset(sprite,"Assets/Generated/Visuals/"+name+".asset");
            return sprite;
        }
        visualSprites=new Sprite[input.visualFrames.Length];tileSprites=new Sprite[input.visualFrames.Length][];
        for(int i=0;i<visualSprites.Length;i++)
        {
            var f=input.visualFrames[i];
            if(f.tiles!=null&&f.tiles.Length>0)
                tileSprites[i]=f.tiles.Select((t,k)=>Make(f.name+"_t"+k,t.page,t.x,t.y,t.w,t.h)).ToArray();
            else visualSprites[i]=Make(f.name,f.page,f.x,f.y,f.w,f.h);
        }
    }
    static void AddVisuals(Cell cell,Transform parent)
    {
        var spec=input.visualCells.Single(c=>c.name==cell.name);
        var folder=Node("SourceVisualLayers",parent);
        bool hasDepth=spec.groups!=null&&spec.groups.Length>0;
        var groups=new Dictionary<string,Transform>{{"Ground",folder.transform}};
        var renderers=new Dictionary<string,SpriteRenderer>();
        if(hasDepth)
        {
            var center=new Vector3(input.width/input.pixelsPerUnit/2,-input.height/input.pixelsPerUnit/2,0);
            var bg=Node("BGParallax",folder.transform);bg.transform.localPosition=center;
            var ground=Node("Ground",bg.transform);ground.transform.localPosition=-center;
            groups["Ground"]=ground.transform;
            var levels=new List<BGParallax.ParallaxLayer>{new BGParallax.ParallaxLayer{Speed=0,Layer=ground}};
            foreach(var group in spec.groups)
            {
                if(!spec.layers.Any(l=>l.group==group.name))continue;
                bool isDepth=group.kind=="depth"||group.kind=="actor";
                var node=Node(group.name,isDepth||group.kind=="fixed"?ground.transform:bg.transform);
                if(isDepth)
                {
                    node.transform.localPosition=Pos(group.x,group.y);
                    var sorting=node.AddComponent<SortingGroup>();sorting.sortingLayerName="Default";
                    sorting.sortingOrder=spec.layers.Where(l=>l.group==group.name).Min(l=>l.order);
                    var depth=node.AddComponent<ZOffset>();depth.rootTransform=node.transform;depth.sortingGroup=sorting;
                }
                else if(group.kind!="fixed")  // parallax and sway (banners ride their wall's parallax speed)
                {
                    node.transform.localPosition=-center;
                    levels.Add(new BGParallax.ParallaxLayer{Speed=group.speed,Layer=node});
                }
                groups[group.name]=node.transform;
            }
            bg.AddComponent<BGParallax>().levels=levels.ToArray();
        }
        clocks[cell.name]=new List<VisualClock>();
        foreach(var layer in spec.layers)
        {
            var owner=hasDepth?groups[layer.group]:folder.transform;
            var frame=input.visualFrames[layer.frames[0]];
            if(tileSprites[layer.frames[0]]!=null)
            {
                // Sparse static image: one renderer per stored tile, at the tile's place in the original image.
                if(layer.period!=1)throw new Exception("Animated layer frames are never tiled: "+layer.name);
                var node=Node(layer.name,owner);
                node.transform.position=parent.TransformPoint(new Vector3(layer.x/input.pixelsPerUnit,-layer.y/input.pixelsPerUnit,5));
                float ppu=input.pixelsPerUnit*Mathf.Max(1,input.rasterScale);
                for(int k=0;k<frame.tiles.Length;k++)
                {
                    var t=frame.tiles[k];var tile=Node(layer.name+"_t"+k,node.transform).AddComponent<SpriteRenderer>();
                    tile.sprite=tileSprites[layer.frames[0]][k];tile.sortingOrder=layer.order;
                    tile.sortingLayerName=hasDepth&&owner.GetComponent<ZOffset>()==null?"BGFront":"Default";
                    tile.transform.localPosition=new Vector3((t.ox+t.w/2f-frame.w/2f)/ppu,-(t.oy+t.h/2f-frame.h/2f)/ppu,0);
                }
                continue;
            }
            var sr=Node(layer.name,owner).AddComponent<SpriteRenderer>();
            sr.sprite=visualSprites[layer.frames[0]];sr.sortingOrder=layer.order;
            sr.sortingLayerName=hasDepth&&owner.GetComponent<ZOffset>()==null?"BGFront":"Default";
            sr.transform.position=parent.TransformPoint(new Vector3(layer.x/input.pixelsPerUnit,-layer.y/input.pixelsPerUnit,5));
            renderers[layer.name]=sr;
        }
        AddSway(spec,parent,groups);
        AddEffects(spec,cell,parent,hasDepth?groups["Ground"]:folder.transform,groups);
        string controllerPath="Assets/Generated/Visuals/"+cell.name+"_Clocks.controller";
        var controller=AssetDatabase.LoadAssetAtPath<AnimatorController>(controllerPath)??AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
        int layerIndex=0;
        foreach(var group in spec.layers.Where(l=>l.period>1).GroupBy(l=>l.period))
        {
            var clip=new AnimationClip{name=cell.name+"_Clock_"+group.Key,frameRate=input.fps};
            foreach(var layer in group)
            {
                var keys=new ObjectReferenceKeyframe[layer.period+1];
                for(int f=0;f<=layer.period;f++) keys[f]=new ObjectReferenceKeyframe{time=f/input.fps,value=visualSprites[layer.frames[f%layer.period]]};
                AnimationUtility.SetObjectReferenceCurve(clip,EditorCurveBinding.PPtrCurve(AnimationUtility.CalculateTransformPath(renderers[layer.name].transform,parent),typeof(SpriteRenderer),"m_Sprite"),keys);
            }
            var settings=AnimationUtility.GetAnimationClipSettings(clip);settings.loopTime=true;
            settings.startTime=0;settings.stopTime=group.Key/input.fps;
            AnimationUtility.SetAnimationClipSettings(clip,settings);
            AssetDatabase.CreateAsset(clip,"Assets/Generated/Visuals/"+clip.name+".anim");
            if(layerIndex>=controller.layers.Length)controller.AddLayer("Clock_"+group.Key);
            var layers=controller.layers;layers[layerIndex].defaultWeight=1;controller.layers=layers;
            var machine=controller.layers[layerIndex].stateMachine;
            var state=machine.states.Length==0?machine.AddState("SourceLoop"):machine.states[0].state;
            state.motion=clip;state.writeDefaultValues=false;machine.defaultState=state;
            clocks[cell.name].Add(new VisualClock{go=parent.gameObject,clip=clip,period=group.Key});layerIndex++;
        }
        if(swayClips.TryGetValue(cell.name,out var sway))
        {
            if(layerIndex>=controller.layers.Length)controller.AddLayer("BannerSway");
            var swayLayers=controller.layers;swayLayers[layerIndex].defaultWeight=1;controller.layers=swayLayers;
            var swayMachine=controller.layers[layerIndex].stateMachine;
            var swayState=swayMachine.states.Length==0?swayMachine.AddState("Sway"):swayMachine.states[0].state;
            swayState.motion=sway.clip;swayState.writeDefaultValues=false;swayMachine.defaultState=swayState;layerIndex++;
        }
        var animator=parent.gameObject.AddComponent<Animator>();animator.runtimeAnimatorController=controller;
        animator.cullingMode=AnimatorCullingMode.AlwaysAnimate;
    }
    static Material EffectMaterial(Material source,string sourceName)
    {
        if(effectMaterials.TryGetValue(sourceName+"/"+source.name,out var made))return made;
        // Stock particle shaders ship with every Unity player. The URP sprite shader (fireball core) has no
        // equivalent in this built-in project; unlit, it is the same texture x vertex colour alpha blend.
        string name=source.shader.name;
        // Custom AE shaders (Hovl center glow, Shader Graph erosion smoke) are not in this project either: additive
        // glows map to the stock additive particle shader, everything else to stock alpha blending.
        string stock=name.StartsWith("Mobile/Particles/")||name.StartsWith("Legacy Shaders/Particles/")?name
            :name.Contains("Add")?"Mobile/Particles/Additive":"Mobile/Particles/Alpha Blended";
        var shader=Shader.Find(stock);
        if(shader==null)throw new Exception("Particle shader unavailable: "+name);
        var material=new Material(shader){name=source.name};
        if(source.mainTexture!=null)
        {
            var art=AssetDatabase.LoadAssetAtPath<Texture2D>(input.effectArtDir+"/"+sourceName+"/"+source.mainTexture.name+".png");
            if(art==null)throw new Exception("Extracted effect texture missing: "+source.mainTexture.name);
            material.mainTexture=art;material.mainTextureScale=source.mainTextureScale;material.mainTextureOffset=source.mainTextureOffset;
        }
        foreach(var property in new[]{"_TintColor","_Color"})
            if(source.HasProperty(property)&&material.HasProperty(property))material.SetColor(property,source.GetColor(property));
        Directory.CreateDirectory("Assets/Generated/Effects");
        AssetDatabase.CreateAsset(material,"Assets/Generated/Effects/"+sourceName+"_"+string.Concat(source.name.Select(c=>char.IsLetterOrDigit(c)?c:'_'))+".mat");
        effectMaterials[sourceName+"/"+source.name]=material;return material;
    }
    static GameObject PortParticles(GameObject source,Transform parent,string sourceName)
    {
        var node=Node(source.name,parent);
        node.transform.localPosition=source.transform.localPosition;node.transform.localRotation=source.transform.localRotation;
        node.transform.localScale=source.transform.localScale;
        var system=source.GetComponent<ParticleSystem>();var renderer=source.GetComponent<ParticleSystemRenderer>();
        if(system!=null&&renderer!=null&&renderer.sharedMaterial!=null)
        {
            var copy=node.AddComponent<ParticleSystem>();EditorUtility.CopySerialized(system,copy);
            var copyRenderer=node.GetComponent<ParticleSystemRenderer>();EditorUtility.CopySerialized(renderer,copyRenderer);
            copyRenderer.sharedMaterial=EffectMaterial(renderer.sharedMaterial,sourceName);copyRenderer.trailMaterial=null;
            var main=copy.main;main.scalingMode=ParticleSystemScalingMode.Hierarchy;main.playOnAwake=true;main.prewarm=main.loop;
        }
        else if(system!=null)skippedEffectParts.Add(source.name);
        foreach(Transform child in source.transform)PortParticles(child.gameObject,node.transform,sourceName);
        return node;
    }
    static float PlumeAngle(ParticleSystem system,float seconds)
    {
        // Direction from the emitter to the particles' centroid, in degrees (90 = straight up).
        system.Simulate(seconds,true,true);
        var particles=new ParticleSystem.Particle[system.particleCount];int count=system.GetParticles(particles);
        bool local=system.main.simulationSpace==ParticleSystemSimulationSpace.Local;Vector3 sum=Vector3.zero;
        for(int i=0;i<count;i++)sum+=local?system.transform.TransformPoint(particles[i].position):particles[i].position;
        system.Clear(true);
        if(count==0)return 90;
        var d=sum/count-system.transform.position;return Mathf.Atan2(d.y,d.x)*Mathf.Rad2Deg;
    }
    static Vector2 ParticleExtent(ParticleSystem system,float seconds)
    {
        // World width/height covered by this system's own particles (children such as the soft glow excluded).
        system.Simulate(seconds,true,true);
        var particles=new ParticleSystem.Particle[system.particleCount];int count=system.GetParticles(particles);
        if(count==0)return Vector2.zero;
        bool local=system.main.simulationSpace==ParticleSystemSimulationSpace.Local;
        Vector2 low=Vector2.positiveInfinity,high=Vector2.negativeInfinity;
        for(int i=0;i<count;i++)
        {
            Vector2 p=local?system.transform.TransformPoint(particles[i].position):particles[i].position;
            float half=particles[i].GetCurrentSize(system)*system.transform.lossyScale.y/2;
            low=Vector2.Min(low,p-Vector2.one*half);high=Vector2.Max(high,p+Vector2.one*half);
        }
        system.Clear(true);return high-low;
    }
    static GameObject SourceNode(string sourceName,string path)
    {
        // A node of a live AE bundle's prefab, by path below the prefab root or by (unique) name anywhere in it.
        if(!effectBundles.TryGetValue(sourceName,out var bundle))
        {
            var source=input.effectSources.Single(e=>e.name==sourceName);
            bundle=AssetBundle.LoadFromFile(source.path);
            if(bundle==null)throw new Exception("Effect source failed to load: "+source.path);
            effectBundles[sourceName]=bundle;
        }
        foreach(var root in bundle.LoadAllAssets<GameObject>())
        {
            var found=path.Contains("/")?root.transform.Find(path):root.GetComponentsInChildren<Transform>(true).FirstOrDefault(t=>t.name==path);
            if(found!=null)return found.gameObject;
        }
        throw new Exception("Effect node missing from "+sourceName+": "+path);
    }
    static Material StockMaterial(string name,string shader,string sourceName,string texture)
    {
        if(effectMaterials.TryGetValue(name,out var material))return material;
        var art=AssetDatabase.LoadAssetAtPath<Texture2D>(input.effectArtDir+"/"+sourceName+"/"+texture+".png");
        if(art==null)throw new Exception("Extracted effect texture missing: "+sourceName+"/"+texture);
        material=new Material(Shader.Find(shader)){name=name,mainTexture=art};Directory.CreateDirectory("Assets/Generated/Effects");
        AssetDatabase.CreateAsset(material,"Assets/Generated/Effects/"+name+".mat");effectMaterials[name]=material;return material;
    }
    static ParticleSystem NewSystem(string name,Transform parent,Vector3 world,Material material,int max)
    {
        var node=Node(name,parent);node.transform.position=world;
        var system=node.AddComponent<ParticleSystem>();system.Stop(true,ParticleSystemStopBehavior.StopEmittingAndClear);
        var main=system.main;main.loop=true;main.prewarm=true;main.playOnAwake=true;main.maxParticles=max;main.startSpeed=0;
        main.simulationSpace=ParticleSystemSimulationSpace.Local;main.scalingMode=ParticleSystemScalingMode.Hierarchy;
        var shape=system.shape;shape.enabled=false;
        var renderer=node.GetComponent<ParticleSystemRenderer>();renderer.sharedMaterial=material;renderer.renderMode=ParticleSystemRenderMode.Billboard;
        return system;
    }
    static void Fade(ParticleSystem system,float inAt,float outAt)
    {
        var fade=system.colorOverLifetime;fade.enabled=true;var gradient=new Gradient();
        gradient.SetKeys(new[]{new GradientColorKey(Color.white,0),new GradientColorKey(Color.white,1)},
                         new[]{new GradientAlphaKey(0,0),new GradientAlphaKey(1,inAt),new GradientAlphaKey(1,outAt),new GradientAlphaKey(0,1)});
        fade.color=gradient;
    }
    static GameObject FlickerGlow(string name,Transform parent,Vector3 world,float size,Color color,float rate)
    {
        // Overlapping short-lived soft glows of varying size: an uneven flame-light flicker (Fire Mage Glow1 art).
        var system=NewSystem(name,parent,world,StockMaterial("FlameLight","Mobile/Particles/Additive","firemage","Glow1"),24);
        var main=system.main;main.startLifetime=new ParticleSystem.MinMaxCurve(.35f,.8f);
        main.startSize=new ParticleSystem.MinMaxCurve(size*.8f,size*1.15f);
        main.startColor=new ParticleSystem.MinMaxGradient(color,new Color(color.r,color.g,color.b,color.a*.6f));
        var emission=system.emission;emission.rateOverTime=rate;Fade(system,.3f,.6f);
        return system.gameObject;
    }
    static void AddEffects(VisualCell spec,Cell cell,Transform parent,Transform ground,Dictionary<string,Transform> groups)
    {
        if(spec.effects==null||spec.effects.Length==0)return;
        var root=Node("NativeEffects",ground);float ppu=input.pixelsPerUnit,width=input.width/ppu;
        var made=new List<(GameObject node,string kind)>();effectNodes[spec.name]=made;
        Vector3 At(float x,float y,float z=4)=>parent.TransformPoint(new Vector3(x/ppu,-y/ppu,z));
        int index=0;
        foreach(var effect in spec.effects)
        {
            index++;
            var owner=!string.IsNullOrEmpty(effect.group)&&groups.TryGetValue(effect.group,out var grouped)?grouped:root.transform;
            var nodes=new List<GameObject>();
            if(effect.kind=="fire")
            {
                var fireSource=SourceNode("firemage",FireSource);
                var node=PortParticles(fireSource,owner,"firemage");node.name="TorchFire_"+effect.depth;
                // Keep the orientation it has inside the monster (its parents turn the emitter), then stand the
                // plume upright over the brazier.
                var sourceRoot=fireSource.transform.root;
                node.transform.rotation=Quaternion.Inverse(sourceRoot.rotation)*fireSource.transform.rotation;
                var system=node.GetComponent<ParticleSystem>();
                float angle=PlumeAngle(system,2);
                node.transform.rotation=Quaternion.AngleAxis(90-angle,Vector3.forward)*node.transform.rotation;
                float upright=PlumeAngle(system,2);
                Check(Mathf.Abs(upright-90)<15,spec.name,"effect_fire_upright",node.name+" plume="+upright);
                // Torch-sized, not bonfire-sized: the monster's fire is far wider than the thin Flash flame, so fit
                // both axes (v6's height-only 1.15x fit read as bonfires in-client). Base sits on the brazier lip.
                node.transform.position=At((effect.x0+effect.x1)/2,effect.y1-(effect.y1-effect.y0)*.15f);
                var target=new Vector2((effect.x1-effect.x0)/ppu*2.2f,(effect.y1-effect.y0)/ppu*.6f);
                var measured=ParticleExtent(system,2);
                Check(measured.y>0,spec.name,"effect_fire_emits",node.name);
                node.transform.localScale*=Mathf.Min(target.x/Mathf.Max(measured.x,.001f),target.y/Mathf.Max(measured.y,.001f));
                var fitted=ParticleExtent(system,2);
                Check(fitted.x<=target.x*1.3f&&fitted.y<=target.y*1.3f&&fitted.y>=target.y*.4f,spec.name,"effect_fire_fit",
                      node.name+" target="+target+" fitted="+fitted);
                nodes.Add(node);made.Add((node,"fire"));
                // The light it casts on the wall around it: behind the flame, in the same painter slot.
                var light=FlickerGlow("TorchLight_"+effect.depth,owner,At((effect.x0+effect.x1)/2,effect.y0+(effect.y1-effect.y0)*.55f,4.5f),
                                      fitted.y*2.6f,new Color(1,.55f,.22f,.30f),7);
                nodes.Add(light);made.Add((light,"torch_light"));
            }
            else if(effect.kind=="smoke"){var node=GroundSmoke(cell,parent,owner);nodes.Add(node);made.Add((node,"smoke"));}
            else if(effect.kind=="dracolich_eye")
            {
                // AE's dracolich eye glow (Dracolich_Bone), ported as-is. Its eye smoke is one 999 s particle that AE's
                // erosion shader dissolves; on a stock shader it is a permanent streak, so the smoke is rebuilt as
                // short red wisps from the same texture and colour, curling up out of the socket.
                var node=Node("DracolichEye_"+index,owner);node.transform.position=At(effect.x,effect.y);
                var glow=PortParticles(SourceNode("dracolich","-Artix- DragonEyeVFX-02-Right"),node.transform,"dracolich");
                glow.transform.localPosition=Vector3.zero;glow.transform.localRotation=Quaternion.identity;
                var extent=ParticleExtent(glow.GetComponent<ParticleSystem>(),2);
                Check(extent.x>0,spec.name,"effect_eye_emits",node.name);
                glow.transform.localScale*=effect.size*1.7f/ppu/Mathf.Max(extent.x,.001f);
                // Seen only through the socket: a mask of the hole's own dark interior (build_map_prototype.socket_mask).
                var importer=(TextureImporter)AssetImporter.GetAtPath(effect.mask);
                importer.textureType=TextureImporterType.Sprite;importer.spriteImportMode=SpriteImportMode.Single;
                importer.spritePixelsPerUnit=ppu*Mathf.Max(1,input.rasterScale);importer.mipmapEnabled=false;
                importer.alphaIsTransparency=true;importer.filterMode=FilterMode.Bilinear;importer.SaveAndReimport();
                var socket=Node("SocketMask",node.transform).AddComponent<SpriteMask>();
                socket.sprite=AssetDatabase.LoadAssetAtPath<Sprite>(effect.mask);socket.alphaCutoff=.25f;
                socket.transform.position=node.transform.position;
                Check(socket.sprite!=null,spec.name,"effect_eye_socket_mask",effect.mask);
                foreach(var r in glow.GetComponentsInChildren<ParticleSystemRenderer>())r.maskInteraction=SpriteMaskInteraction.VisibleInsideMask;
                var wisps=NewSystem("EyeSmoke",node.transform,node.transform.position+new Vector3(0,0,-.1f),
                                    StockMaterial("DracolichEyeSmoke","Mobile/Particles/Alpha Blended","dracolich","Smoke01ATex"),30);
                var wmain=wisps.main;wmain.startLifetime=new ParticleSystem.MinMaxCurve(1.6f,2.8f);
                float eye=effect.size/ppu;wmain.startSize=new ParticleSystem.MinMaxCurve(eye*.22f,eye*.4f);
                wmain.startRotation=new ParticleSystem.MinMaxCurve(0,Mathf.PI*2);
                wmain.startColor=new ParticleSystem.MinMaxGradient(new Color(1,.19f,.19f,.32f),new Color(.8f,.12f,.12f,.2f));
                var wemission=wisps.emission;wemission.rateOverTime=3;
                var wvelocity=wisps.velocityOverLifetime;wvelocity.enabled=true;wvelocity.space=ParticleSystemSimulationSpace.Local;
                wvelocity.x=new ParticleSystem.MinMaxCurve(.05f,.35f);wvelocity.y=new ParticleSystem.MinMaxCurve(.35f,.75f);wvelocity.z=new ParticleSystem.MinMaxCurve(0,0);
                var wsize=wisps.sizeOverLifetime;wsize.enabled=true;wsize.size=new ParticleSystem.MinMaxCurve(1,AnimationCurve.Linear(0,.6f,1,1.8f));
                var wnoise=wisps.noise;wnoise.enabled=true;wnoise.strength=.25f;wnoise.frequency=.8f;
                Fade(wisps,.4f,.6f);  // unseen as it leaves the socket, visible only once it has risen out
                nodes.Add(node);made.Add((node,"dracolich_eye"));
            }
            else if(effect.kind=="lantern")
            {
                var node=FlickerGlow("LanternLight_"+index,owner,At(effect.x,effect.y),effect.size*3.2f/ppu,new Color(1,.78f,.45f,.40f),9);
                nodes.Add(node);made.Add((node,"lantern"));
            }
            else if(effect.kind=="bats")
            {
                // AQW's skeletal Bat2 "Walk" flap loop (baked from its Flash vectors), crossing the night sky.
                var material=StockMaterial("SkyBats","Mobile/Particles/Alpha Blended","bat","BatFlap");
                float top=effect.y0/ppu,bottom=effect.y1/ppu,speed=3.2f,life=(width+8)/speed;
                var system=NewSystem("SkyBats",owner,At(-40,(effect.y0+effect.y1)/2),material,14);
                var main=system.main;main.duration=12;main.startLifetime=life;main.startSize=new ParticleSystem.MinMaxCurve(3.6f,6f);  // sheet cells pad the bat: ~25-35 source px of bat
                main.startColor=new ParticleSystem.MinMaxGradient(new Color(.82f,.80f,.80f,1),new Color(.66f,.64f,.66f,1));
                var shape=system.shape;shape.enabled=true;shape.shapeType=ParticleSystemShapeType.Box;shape.scale=new Vector3(.1f,bottom-top,.1f);
                var emission=system.emission;emission.rateOverTime=0;
                var burst=new ParticleSystem.Burst(0,1,3,0,12);burst.probability=.75f;emission.SetBursts(new[]{burst});
                var velocity=system.velocityOverLifetime;velocity.enabled=true;velocity.space=ParticleSystemSimulationSpace.Local;
                velocity.x=new ParticleSystem.MinMaxCurve(speed*.8f,speed*1.25f);velocity.y=new ParticleSystem.MinMaxCurve(-.25f,.25f);velocity.z=new ParticleSystem.MinMaxCurve(0,0);
                var noise=system.noise;noise.enabled=true;noise.strength=.35f;noise.frequency=.25f;noise.scrollSpeed=.2f;
                var sheet=system.textureSheetAnimation;sheet.enabled=true;sheet.numTilesX=input.batCols;sheet.numTilesY=input.batRows;
                sheet.animation=ParticleSystemAnimationType.WholeSheet;
                sheet.frameOverTime=new ParticleSystem.MinMaxCurve((float)input.batFrames/(input.batCols*input.batRows),AnimationCurve.Linear(0,0,1,1));
                sheet.cycleCount=Mathf.Max(1,Mathf.RoundToInt(life*input.fps/input.batFrames));
                Fade(system,.03f,.97f);
                nodes.Add(system.gameObject);made.Add((system.gameObject,"bats"));
            }
            else if(effect.kind=="ash")
            {
                // Ash drifting down over the fallen fortress, carried by the same left-to-right air as the ground smoke.
                var material=StockMaterial("FallingAsh","Mobile/Particles/Alpha Blended","firemage","simpleFlash");
                var system=NewSystem("FallingAsh",owner,At(input.width/2,-30),material,220);
                float fall=.75f,life=(input.height/ppu+6)/fall;
                var main=system.main;main.duration=10;main.startLifetime=life;main.startSize=new ParticleSystem.MinMaxCurve(.22f,.5f);
                main.startColor=new ParticleSystem.MinMaxGradient(new Color(.62f,.60f,.58f,.75f),new Color(.40f,.38f,.38f,.55f));
                var shape=system.shape;shape.enabled=true;shape.shapeType=ParticleSystemShapeType.Box;shape.scale=new Vector3(width*1.3f,.1f,.1f);
                var emission=system.emission;emission.rateOverTime=2.4f*Mathf.Max(.05f,effect.density);
                var velocity=system.velocityOverLifetime;velocity.enabled=true;velocity.space=ParticleSystemSimulationSpace.Local;
                velocity.x=new ParticleSystem.MinMaxCurve(.2f,.7f);velocity.y=new ParticleSystem.MinMaxCurve(-fall*1.25f,-fall*.8f);velocity.z=new ParticleSystem.MinMaxCurve(0,0);
                var noise=system.noise;noise.enabled=true;noise.strength=.45f;noise.frequency=.35f;noise.scrollSpeed=.3f;
                Fade(system,.04f,.92f);
                nodes.Add(system.gameObject);made.Add((system.gameObject,"ash"));
            }
            else if(effect.kind=="cinders")
            {
                // A few cinders still rising from the crash rubble.
                var material=StockMaterial("RisingCinders","Mobile/Particles/Additive","firemage","simpleFlash");
                var system=NewSystem("RisingCinders",owner,At((effect.x0+effect.x1)/2,effect.y),material,40);
                var main=system.main;main.duration=5;main.startLifetime=new ParticleSystem.MinMaxCurve(2.5f,4.5f);
                main.startSize=new ParticleSystem.MinMaxCurve(.12f,.28f);
                main.startColor=new ParticleSystem.MinMaxGradient(new Color(1,.62f,.22f,1),new Color(1,.35f,.12f,.85f));
                var shape=system.shape;shape.enabled=true;shape.shapeType=ParticleSystemShapeType.Box;shape.scale=new Vector3((effect.x1-effect.x0)/ppu,.6f,.1f);
                var emission=system.emission;emission.rateOverTime=2.2f;
                var velocity=system.velocityOverLifetime;velocity.enabled=true;velocity.space=ParticleSystemSimulationSpace.Local;
                velocity.x=new ParticleSystem.MinMaxCurve(.1f,.5f);velocity.y=new ParticleSystem.MinMaxCurve(.5f,1.2f);velocity.z=new ParticleSystem.MinMaxCurve(0,0);
                var noise=system.noise;noise.enabled=true;noise.strength=.6f;noise.frequency=.6f;
                Fade(system,.1f,.6f);
                nodes.Add(system.gameObject);made.Add((system.gameObject,"cinders"));
            }
            else throw new Exception("Unknown map effect: "+effect.kind);
            // Painter slot: anchored effects (replaced art, or 'after' a layer) sit just above the layer below them
            // (same order, nearer z); free effects (smoke, ash, cinders) go above all the art.
            bool anchored=effect.kind=="fire"||!string.IsNullOrEmpty(effect.after);
            foreach(var node in nodes)foreach(var r in node.GetComponentsInChildren<ParticleSystemRenderer>())
            {r.sortingLayerName="BGFront";r.sortingOrder=anchored?effect.order-1:effect.order;}
        }
    }
    static void AddSway(VisualCell spec,Transform parent,Dictionary<string,Transform> groups)
    {
        // Banners hang from their top edge and sway gently; each rides its wall's parallax, so only a pivot turns.
        var sway=(spec.groups??new VisualGroup[0]).Where(g=>g.kind=="sway"&&groups.ContainsKey(g.name)).ToArray();
        if(sway.Length==0)return;
        var clip=new AnimationClip{name=spec.name+"_BannerSway",frameRate=input.fps};const float period=3.6f,amplitude=1.4f;
        foreach(var group in sway)
        {
            var node=groups[group.name];var art=node.GetComponentsInChildren<SpriteRenderer>();
            var bounds=art[0].bounds;foreach(var r in art)bounds.Encapsulate(r.bounds);
            var pivot=Node("SwayPivot",node);pivot.transform.position=new Vector3(bounds.center.x,bounds.max.y,node.position.z);
            foreach(var child in node.Cast<Transform>().Where(t=>t!=pivot.transform).ToArray())child.SetParent(pivot.transform,true);
            var keys=new Keyframe[9];
            for(int k=0;k<9;k++)keys[k]=new Keyframe(k/8f*period,amplitude*Mathf.Sin(2*Mathf.PI*(k/8f+group.phase)));
            var curve=new AnimationCurve(keys);for(int k=0;k<9;k++)curve.SmoothTangents(k,0);
            clip.SetCurve(AnimationUtility.CalculateTransformPath(pivot.transform,parent),typeof(Transform),"localEulerAnglesRaw.z",curve);
        }
        var settings=AnimationUtility.GetAnimationClipSettings(clip);settings.loopTime=true;settings.stopTime=period;
        AnimationUtility.SetAnimationClipSettings(clip,settings);
        AssetDatabase.CreateAsset(clip,"Assets/Generated/Visuals/"+clip.name+".anim");
        swayClips[spec.name]=(clip,sway.Length);
    }
    static GameObject GroundSmoke(Cell cell,Transform parent,Transform root)
    {
        // Low smoke rolling left to right across the walkable band of the room: new presentation, AE smoke texture.
        float ppu=input.pixelsPerUnit,width=input.width/ppu;
        // Flash walk areas cover the whole stage (blockers carve the floor), so the floor band runs from just
        // above the highest spawn pad to the bottom of the stage.
        float top=(cell.spawns.Min(p=>p.y)-60)/ppu,bottom=input.height/ppu,speed=1.1f;
        var node=Node("GroundSmoke",root);node.transform.position=parent.TransformPoint(new Vector3(-4,-(top+bottom)/2,4));
        var system=node.AddComponent<ParticleSystem>();system.Stop(true,ParticleSystemStopBehavior.StopEmittingAndClear);
        var main=system.main;main.loop=true;main.prewarm=true;main.duration=10;main.playOnAwake=true;
        main.startLifetime=(width+10)/speed;main.startSpeed=0;main.startSize=new ParticleSystem.MinMaxCurve(6,14);
        main.startRotation=new ParticleSystem.MinMaxCurve(0,Mathf.PI*2);main.maxParticles=160;
        main.startColor=new ParticleSystem.MinMaxGradient(new Color(.70f,.74f,.68f,.42f),new Color(.52f,.56f,.52f,.28f));
        main.simulationSpace=ParticleSystemSimulationSpace.Local;main.scalingMode=ParticleSystemScalingMode.Hierarchy;
        var emission=system.emission;emission.rateOverTime=48/main.startLifetime.constant;
        var shape=system.shape;shape.shapeType=ParticleSystemShapeType.Box;shape.scale=new Vector3(.1f,Mathf.Max(2,bottom-top),.1f);
        var velocity=system.velocityOverLifetime;velocity.enabled=true;velocity.space=ParticleSystemSimulationSpace.Local;
        velocity.x=new ParticleSystem.MinMaxCurve(speed*.8f,speed*1.2f);velocity.y=new ParticleSystem.MinMaxCurve(-.06f,.06f);velocity.z=new ParticleSystem.MinMaxCurve(0,0);
        var spin=system.rotationOverLifetime;spin.enabled=true;spin.z=new ParticleSystem.MinMaxCurve(-.12f,.12f);
        var fade=system.colorOverLifetime;fade.enabled=true;var gradient=new Gradient();
        gradient.SetKeys(new[]{new GradientColorKey(Color.white,0),new GradientColorKey(Color.white,1)},
                         new[]{new GradientAlphaKey(0,0),new GradientAlphaKey(1,.12f),new GradientAlphaKey(1,.85f),new GradientAlphaKey(0,1)});
        fade.color=gradient;
        var shader=Shader.Find("Mobile/Particles/Alpha Blended");
        var art=AssetDatabase.LoadAssetAtPath<Texture2D>(input.effectArtDir+"/firemage/simpleSmoke2.png")??AssetDatabase.LoadAssetAtPath<Texture2D>(input.effectArtDir+"/firemage/Smoke26.png");
        if(art==null)throw new Exception("Extracted smoke texture missing");
        if(!effectMaterials.TryGetValue("GroundSmoke",out var material))
        {
            material=new Material(shader){name="GroundSmoke",mainTexture=art};Directory.CreateDirectory("Assets/Generated/Effects");
            AssetDatabase.CreateAsset(material,"Assets/Generated/Effects/GroundSmoke.mat");effectMaterials["GroundSmoke"]=material;
        }
        var renderer=node.GetComponent<ParticleSystemRenderer>();renderer.sharedMaterial=material;renderer.renderMode=ParticleSystemRenderMode.Billboard;
        return node;
    }
    static void SimulateEffects(Transform cell,bool clear)
    {
        foreach(var system in cell.GetComponentsInChildren<ParticleSystem>().Where(s=>s.transform.parent.GetComponent<ParticleSystem>()==null))
        {
            if(clear)system.Clear(true);
            else system.Simulate(Mathf.Clamp(system.main.startLifetime.constantMax*.8f,2,90),true,true);
        }
    }
    public static void BuildAndroid()
    {
        try
        {
            string[] args=Environment.GetCommandLineArgs();
            input=JsonUtility.FromJson<InputData>(File.ReadAllText(args[Array.IndexOf(args,"-mapInput")+1]));
            var prefab=AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Generated/map-shadowfall.prefab");
            if(prefab==null || prefab.GetComponentsInChildren<MapCell>(true).Length!=2)
                throw new Exception("Verified Windows prototype prefab is required first");
            string output=Path.Combine(input.outputDir,"android"); Directory.CreateDirectory(output);
            var manifest=BuildPipeline.BuildAssetBundles(output,new[]{new AssetBundleBuild{assetBundleName="map-shadowfall.prototype.unity3d",assetNames=new[]{"Assets/Generated/map-shadowfall.prefab"}}},BuildAssetBundleOptions.ChunkBasedCompression,BuildTarget.Android);
            if(manifest==null || !BuildPipeline.GetCRCForAssetBundle(Path.Combine(output,"map-shadowfall.prototype.unity3d"),out uint crc))
                throw new Exception("Android map bundle build failed");
            File.WriteAllText(Path.Combine(output,"build_contract.json"),"{\"status\":\"passed\",\"platform\":\"android\",\"crc\":"+crc+",\"device_render_verified\":false}");
            Debug.Log("MAP_ANDROID_BUILD_PASS crc="+crc); EditorApplication.Exit(0);
        }
        catch(Exception e) { Debug.LogError("MAP_ANDROID_BUILD_FAILED "+e); EditorApplication.Exit(1); }
    }
    public static void Build()
    {
        try
        {
            string[] args=Environment.GetCommandLineArgs();
            input=JsonUtility.FromJson<InputData>(File.ReadAllText(args[Array.IndexOf(args,"-mapInput")+1]));
            PlayerSettings.colorSpace=ColorSpace.Gamma;
            Layers();
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);
            var root=new GameObject("map-shadowfall");
            root.AddComponent<Pixelplacement.Initialization>();
            var map=root.AddComponent<MapPrefab>(); map.CurrentCell="Enter";
            if(input.layered) PrepareVisualSprites();
            foreach(var spec in input.cells)
            {
                var go=Node(spec.name,root.transform); go.AddComponent<Pixelplacement.State>();
                var cell=go.AddComponent<MapCell>(); cell.ParentMap=map;
                cell.entityScale=spec.sourceScale; cell.entitySpeed=spec.sourceSpeed;
                cell.blockerColor=new Color32(0,0,0,255);
                cell.OverrideZoom=true; cell.zoom=input.cameraZoom>0?input.cameraZoom:input.height/input.pixelsPerUnit/2;
                var confiner=Node("ConfinerCollider",go.transform).AddComponent<BoxCollider>();
                confiner.center=new Vector3(input.width/input.pixelsPerUnit/2,-input.height/input.pixelsPerUnit/2,0);
                confiner.size=new Vector3(input.width/input.pixelsPerUnit,input.height/input.pixelsPerUnit,0);
                cell.ConfinerCollider=confiner;
                if(input.layered) AddVisuals(spec,go.transform);
                else {
                var importer=(TextureImporter)AssetImporter.GetAtPath(spec.image);
                importer.textureType=TextureImporterType.Sprite; importer.spriteImportMode=SpriteImportMode.Single;
                importer.spritePixelsPerUnit=input.pixelsPerUnit;
                importer.textureCompression=TextureImporterCompression.Uncompressed;
                importer.mipmapEnabled=false; importer.filterMode=FilterMode.Point;
                importer.maxTextureSize=2048; importer.SaveAndReimport();
                var art=Node("SourceSnapshot_AnimationPending",go.transform).AddComponent<SpriteRenderer>();
                art.sprite=AssetDatabase.LoadAssetAtPath<Sprite>(spec.image);
                Check(art.sprite!=null,spec.name,"snapshot_sprite_import",spec.image);
                art.transform.localPosition=confiner.center+new Vector3(0,0,5);
                }
                var ground=Node("Walkable",go.transform);
                foreach(var polygon in spec.walkables)
                {
                    var collider=PolygonObject("ClickWalk",ground.transform,polygon);
                    collider.gameObject.layer=21; collider.gameObject.AddComponent<ClickableWalk>();
                }
                var solids=Node("Blockers",go.transform);
                foreach(var polygon in spec.blockers)
                {
                    var collider=PolygonObject("SourceSolid",solids.transform,polygon);
                    collider.gameObject.layer=20; collider.gameObject.AddComponent<MapBlocker>();
                }
                var pads=Node("Spawns",go.transform);
                foreach(var spawn in spec.spawns)
                {
                    var pad=Node(spawn.name,pads.transform); pad.transform.localPosition=Pos(spawn.x,spawn.y);
                    pad.AddComponent<MapSpawn>();
                }
                cell.SpawnPads=pads.GetComponentsInChildren<MapSpawn>();
                var exits=Node("Exits",go.transform);
                foreach(var exit in spec.exits) foreach(var polygon in exit.polygons)
                {
                    var collider=PolygonObject(exit.name,exits.transform,polygon); collider.isTrigger=true; collider.gameObject.layer=10;
                    var trigger=collider.gameObject.AddComponent<MapGoToCell>(); trigger.TargetCell=exit.targetCell; trigger.TargetSpawnPad=exit.targetPad;
                }
                go.SetActive(spec.name=="Enter");
            }
            map.currentState=root.transform.Find("Enter").gameObject;
            map.defaultState=map.currentState;
            var camera=new GameObject("PrototypeCamera").AddComponent<Camera>(); camera.tag="MainCamera";
            camera.orthographic=true; camera.orthographicSize=input.height/input.pixelsPerUnit/2;
            camera.aspect=input.width/input.height;
            camera.transform.position=new Vector3(input.width/input.pixelsPerUnit/2,-input.height/input.pixelsPerUnit/2,-10);
            camera.clearFlags=CameraClearFlags.SolidColor; camera.backgroundColor=Color.black;
            var probe=new GameObject("LocalNavigationProbe_NotIncludedInBundle").AddComponent<PreviewProbe>();
            probe.map=root; probe.view=camera;
            var marker=Node("ProbeMarker",probe.transform).AddComponent<SpriteRenderer>();
            var tex=new Texture2D(12,12,TextureFormat.RGBA32,false);
            for(int y=0;y<12;y++) for(int x=0;x<12;x++) tex.SetPixel(x,y,(new Vector2(x-5.5f,y-5.5f).sqrMagnitude<27)?Color.yellow:Color.clear);
            tex.Apply(); AssetDatabase.CreateAsset(tex,"Assets/ProbeMarker.asset");
            var sprite=Sprite.Create(tex,new Rect(0,0,12,12),new Vector2(.5f,.5f),input.pixelsPerUnit);
            AssetDatabase.CreateAsset(sprite,"Assets/ProbeMarkerSprite.asset"); marker.sprite=sprite; marker.sortingOrder=100;
            probe.marker=marker.transform;
            foreach(var spec in input.cells)
            {
                probe.Enter(spec.name,spec.spawns[0].name); marker.enabled=false;
                if(input.layered)
                {
                    // Art-reference captures use authored painter order.
                    // Player-relative sorting is exercised independently below.
                    var cellRoot=root.transform.Find(spec.name);
                    var sortingGroups=cellRoot.GetComponentsInChildren<SortingGroup>();
                    var sprites=cellRoot.GetComponentsInChildren<SpriteRenderer>();
                    var layers=sprites.Select(r=>r.sortingLayerID).ToArray();
                    foreach(var sg in sortingGroups)sg.enabled=false;
                    foreach(var r in sprites)r.sortingLayerName="BGFront";
                    foreach(int frame in input.sampleFrames)
                    {
                        foreach(var clock in clocks[spec.name]) clock.clip.SampleAnimation(clock.go,(frame%clock.period)/input.fps);
                        Capture(camera,spec.name+"-frame-"+frame);
                    }
                    foreach(var clock in clocks[spec.name]) clock.clip.SampleAnimation(clock.go,0);
                    foreach(var sg in sortingGroups)sg.enabled=true;
                    for(int i=0;i<sprites.Length;i++)sprites[i].sortingLayerID=layers[i];
                    var effectSpec=input.visualCells.Single(c=>c.name==spec.name).effects??new Effect[0];
                    if(effectSpec.Length>0)
                    {
                        // Art references above exclude effects (never simulated there); this preview shows them live.
                        SimulateEffects(cellRoot,false);
                        foreach(var made in effectNodes[spec.name])
                            Check(made.node.GetComponentsInChildren<ParticleSystem>().Sum(ps=>ps.particleCount)>0,spec.name,"effect_"+made.kind+"_live",
                                  made.node.name+" n="+made.node.GetComponentsInChildren<ParticleSystem>().Sum(ps=>ps.particleCount)
                                  +" at="+string.Join(";",made.node.GetComponentsInChildren<ParticleSystemRenderer>().Select(r=>r.bounds.ToString("F1"))));
                        if(swayClips.TryGetValue(spec.name,out var swayed))
                            Check(AnimationUtility.GetCurveBindings(swayed.clip).Select(b=>b.path).Distinct().Count()==swayed.banners,spec.name,"effect_banner_sway_bound",swayed.banners.ToString());
                        var fires=cellRoot.GetComponentsInChildren<ParticleSystem>().Where(ps=>ps.name.StartsWith("TorchFire_")).ToArray();
                        Check(fires.Length==effectSpec.Count(e=>e.kind=="fire"),spec.name,"effect_fire_count",fires.Length.ToString());
                        foreach(var fire in fires)Check(fire.particleCount>0,spec.name,"effect_fire_live",fire.name);
                        var smoke=cellRoot.GetComponentsInChildren<ParticleSystem>().SingleOrDefault(ps=>ps.name=="GroundSmoke");
                        if(effectSpec.Any(e=>e.kind=="smoke"))Check(smoke!=null&&smoke.particleCount>10,spec.name,"effect_smoke_live",smoke==null?"missing":smoke.particleCount.ToString());
                        Capture(camera,spec.name+"-effects");
                        var art=cellRoot.GetComponentsInChildren<SpriteRenderer>().Where(r=>r.enabled).ToArray();
                        foreach(var r in art)r.enabled=false;Capture(camera,spec.name+"-effects-only");foreach(var r in art)r.enabled=true;
                        SimulateEffects(cellRoot,true);
                    }
                }
                else Capture(camera,spec.name);
                marker.enabled=true;
                foreach(var spawn in spec.spawns)
                    Check(probe.CanStand(Pos(spawn.x,spawn.y)),spec.name,"spawn_on_free_ground",spawn.name);
                foreach(var collider in root.transform.Find(spec.name).GetComponentsInChildren<PolygonCollider2D>())
                {
                    if(collider.GetComponent<MapBlocker>()==null) continue;
                    // Each pilot blocker is a convex quadrilateral from source SVG.
                    Vector2 center=Vector2.zero; foreach(var p in collider.points) center+=p; center/=collider.points.Length;
                    Check(!probe.CanStand(center),spec.name,"solid_rejects_point",collider.name);
                }
            }
            // Exercise walking and both direction transitions repeatedly with Unity Physics2D.
            probe.Enter("Enter","Spawn");
            for(int cycle=0;cycle<3;cycle++) foreach(string expected in new[]{"Enter","Garden1"})
            {
                Check(probe.currentCell==expected,expected,"current_cell",expected);
                var exit=root.transform.Find(expected).GetComponentInChildren<MapGoToCell>();
                var polygon=exit.GetComponent<PolygonCollider2D>();
                Vector2 target=Vector2.zero; foreach(var p in polygon.points) target+=p; target/=polygon.points.Length;
                var path=probe.FindPath(probe.marker.position,target);
                Check(path.Count>0,expected,"reachable_exit",exit.TargetCell,path.Count);
                foreach(var point in path) { Check(probe.CanStand(point),expected,"walk_sample",exit.TargetCell); probe.marker.position=new Vector3(point.x,point.y,-2); }
                string targetCell=exit.TargetCell;
                Check(probe.TryTransition()&&probe.currentCell==targetCell,expected,"transition",targetCell);
            }
            probe.Enter("Enter","Spawn");
            foreach(var b in effectBundles.Values)b.Unload(true);effectBundles.Clear();
            Check(skippedEffectParts.Count==0,"effects","source_parts_ported",string.Join(",",skippedEffectParts));
            Directory.CreateDirectory("Assets/Generated");
            PrefabUtility.SaveAsPrefabAsset(root,"Assets/Generated/map-shadowfall.prefab");
            EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),"Assets/Generated/ShadowfallPrototype.unity");
            AssetDatabase.SaveAssets();
            BuildPipeline.BuildAssetBundles(input.outputDir,new[]{new AssetBundleBuild{assetBundleName="map-shadowfall.prototype.unity3d",assetNames=new[]{"Assets/Generated/map-shadowfall.prefab"}}},BuildAssetBundleOptions.ChunkBasedCompression,BuildTarget.StandaloneWindows64);
            var bundle=AssetBundle.LoadFromFile(Path.Combine(input.outputDir,"map-shadowfall.prototype.unity3d"));
            Check(bundle!=null,"bundle","load","map-shadowfall");
            var loaded=bundle.LoadAsset<GameObject>("map-shadowfall");
            Check(loaded!=null&&loaded.GetComponent<MapPrefab>()!=null,"bundle","native_root","MapPrefab");
            Check(loaded.GetComponentsInChildren<PreviewProbe>(true).Length==0,"bundle","no_probe_script","PreviewProbe");
            Check(loaded.GetComponentsInChildren<MapCell>(true).Length==input.cells.Length,"bundle","cell_count",input.cells.Length.ToString());
            if(input.layered)
            {
                // Exercise the serialized controller from the loaded bundle,
                // separately from editor clip sampling used for image captures.
                root.SetActive(false);
                marker.enabled=false;
                var instance=UnityEngine.Object.Instantiate(loaded);
                foreach(var spec in input.visualCells)
                {
                    foreach(Transform child in instance.transform) child.gameObject.SetActive(child.name==spec.name);
                    var cell=instance.transform.Find(spec.name);
                    var animator=cell.GetComponent<Animator>();
                    animator.Rebind();animator.Update(0);
                    float previous=0;
                    foreach(int frame in input.sampleFrames)
                    {
                        float time=(frame+.01f)/input.fps;
                        animator.Update(time-previous);previous=time;
                        foreach(var layer in spec.layers.Where(l=>l.period>1))
                        {
                            var sr=cell.GetComponentsInChildren<SpriteRenderer>(true).Single(r=>r.name==layer.name);
                            Check(sr.sprite!=null&&sr.sprite.name=="frame_"+layer.frames[frame%layer.period],spec.name,"loaded_animator_frame",layer.name+"/"+frame);
                        }
                    }
                }
                foreach(var r in instance.GetComponentsInChildren<ParticleSystemRenderer>(true))
                    Check(r.sharedMaterial!=null&&r.sharedMaterial.shader!=null&&r.sharedMaterial.mainTexture!=null,"bundle","effect_bundle_binding",r.name);
                if(input.cameraZoom>0)VerifyDepthAndParallax(instance,camera);
                UnityEngine.Object.DestroyImmediate(instance);
                root.SetActive(true);
            }
            bundle.Unload(true);
            var report=new Report{status="passed",cells=input.cells.Length,checks=checks.ToArray(),proofBoundary=input.layered?"Layered visual timeline build. Source gameplay/visibility scripts remain uncompiled. Point-probe physics; installed-client acceptance separate.":"Local structural prototype only. Snapshot art; source animation and gameplay scripts pending. Physics checks use a point probe and local adapter, not the installed game client."};
            File.WriteAllText(Path.Combine(input.outputDir,"unity_validation.json"),JsonUtility.ToJson(report,true));
            Debug.Log("MAP_PROTOTYPE_PASS cells="+input.cells.Length+" checks="+checks.Count);
            EditorApplication.Exit(0);
        }
        catch(Exception e) { Debug.LogError("MAP_PROTOTYPE_FAILED "+e); EditorApplication.Exit(1); }
    }
}
