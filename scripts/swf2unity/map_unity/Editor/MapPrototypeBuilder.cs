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
    [Serializable] public class VisualGroup { public string name,kind; public float speed,x,y; }
    [Serializable] public class Effect { public string kind; public int depth,order; public float x0,x1,y0,y1; }
    [Serializable] public class VisualCell { public string name; public VisualLayer[] layers; public VisualGroup[] groups; public Effect[] effects; }
    [Serializable] public class InputData { public float width,height,pixelsPerUnit,fps,cameraZoom,rasterScale; public string outputDir,atlas; public Cell[] cells; public bool layered,gpuCompression; public int atlasHeight; public AtlasDef[] visualAtlases; public FrameDef[] visualFrames; public VisualCell[] visualCells; public int[] sampleFrames; public string effectsBundle,effectArtDir; }
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
    static AssetBundle effectBundle;
    static GameObject fireSource;
    static Dictionary<string,Material> effectMaterials=new Dictionary<string,Material>();
    static List<string> skippedEffectParts=new List<string>();
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
            float halfWidth=input.cameraZoom*camera.aspect;
            float left=confiner.bounds.min.x+halfWidth,right=confiner.bounds.max.x-halfWidth;
            Check(right-left>20,spec.name,"camera_horizontal_travel",(right-left).ToString());
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
                else if(group.kind!="fixed")
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
        AddEffects(spec,cell,parent,hasDepth?groups["Ground"]:folder.transform);
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
        var animator=parent.gameObject.AddComponent<Animator>();animator.runtimeAnimatorController=controller;
        animator.cullingMode=AnimatorCullingMode.AlwaysAnimate;
    }
    static Material EffectMaterial(Material source)
    {
        if(effectMaterials.TryGetValue(source.name,out var made))return made;
        // Stock particle shaders ship with every Unity player. The URP sprite shader (fireball core) has no
        // equivalent in this built-in project; unlit, it is the same texture x vertex colour alpha blend.
        string name=source.shader.name;
        var shader=Shader.Find(name.StartsWith("Universal Render Pipeline/2D/Sprite")?"Mobile/Particles/Alpha Blended":name);
        if(shader==null)throw new Exception("Particle shader unavailable: "+name);
        var material=new Material(shader){name=source.name};
        if(source.mainTexture!=null)
        {
            var art=AssetDatabase.LoadAssetAtPath<Texture2D>(input.effectArtDir+"/"+source.mainTexture.name+".png");
            if(art==null)throw new Exception("Extracted effect texture missing: "+source.mainTexture.name);
            material.mainTexture=art;material.mainTextureScale=source.mainTextureScale;material.mainTextureOffset=source.mainTextureOffset;
        }
        foreach(var property in new[]{"_TintColor","_Color"})
            if(source.HasProperty(property)&&material.HasProperty(property))material.SetColor(property,source.GetColor(property));
        Directory.CreateDirectory("Assets/Generated/Effects");
        AssetDatabase.CreateAsset(material,"Assets/Generated/Effects/"+string.Concat(source.name.Select(c=>char.IsLetterOrDigit(c)?c:'_'))+".mat");
        effectMaterials[source.name]=material;return material;
    }
    static GameObject PortParticles(GameObject source,Transform parent)
    {
        var node=Node(source.name,parent);
        node.transform.localPosition=source.transform.localPosition;node.transform.localRotation=source.transform.localRotation;
        node.transform.localScale=source.transform.localScale;
        var system=source.GetComponent<ParticleSystem>();var renderer=source.GetComponent<ParticleSystemRenderer>();
        if(system!=null&&renderer!=null&&renderer.sharedMaterial!=null)
        {
            var copy=node.AddComponent<ParticleSystem>();EditorUtility.CopySerialized(system,copy);
            var copyRenderer=node.GetComponent<ParticleSystemRenderer>();EditorUtility.CopySerialized(renderer,copyRenderer);
            copyRenderer.sharedMaterial=EffectMaterial(renderer.sharedMaterial);copyRenderer.trailMaterial=null;
            var main=copy.main;main.scalingMode=ParticleSystemScalingMode.Hierarchy;main.playOnAwake=true;main.prewarm=main.loop;
        }
        else if(system!=null)skippedEffectParts.Add(source.name);
        foreach(Transform child in source.transform)PortParticles(child.gameObject,node.transform);
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
    static float ParticleHeight(ParticleSystem system,float seconds)
    {
        system.Simulate(seconds,true,true);
        var particles=new ParticleSystem.Particle[system.particleCount];int count=system.GetParticles(particles);
        if(count==0)return 0;
        bool local=system.main.simulationSpace==ParticleSystemSimulationSpace.Local;float low=float.MaxValue,high=float.MinValue;
        for(int i=0;i<count;i++)
        {
            var p=local?system.transform.TransformPoint(particles[i].position):particles[i].position;
            float half=particles[i].GetCurrentSize(system)*system.transform.lossyScale.y/2;
            low=Mathf.Min(low,p.y-half);high=Mathf.Max(high,p.y+half);
        }
        system.Clear(true);return high-low;
    }
    static void AddEffects(VisualCell spec,Cell cell,Transform parent,Transform ground)
    {
        if(spec.effects==null||spec.effects.Length==0)return;
        var root=Node("NativeEffects",ground);float ppu=input.pixelsPerUnit;
        foreach(var effect in spec.effects)
        {
            GameObject node;
            if(effect.kind=="fire")
            {
                if(fireSource==null)
                {
                    effectBundle=AssetBundle.LoadFromFile(input.effectsBundle);
                    var mage=effectBundle.LoadAllAssets<GameObject>().Single(g=>g.name.ToLowerInvariant()=="skeletalfiremage");
                    fireSource=mage.transform.Find(FireSource)?.gameObject??throw new Exception("Fire effect missing from source: "+FireSource);
                }
                node=PortParticles(fireSource,root.transform);node.name="TorchFire_"+effect.depth;
                // Keep the orientation it has inside the monster (its parents turn the emitter), then stand the
                // plume upright over the brazier.
                var sourceRoot=fireSource.transform.root;
                node.transform.rotation=Quaternion.Inverse(sourceRoot.rotation)*fireSource.transform.rotation;
                var system=node.GetComponent<ParticleSystem>();
                float angle=PlumeAngle(system,2);
                node.transform.rotation=Quaternion.AngleAxis(90-angle,Vector3.forward)*node.transform.rotation;
                float upright=PlumeAngle(system,2);
                Check(Mathf.Abs(upright-90)<15,spec.name,"effect_fire_upright",node.name+" plume="+upright);
                // Base of the replaced Flash flame (the brazier lip); size the flame to the one it replaces.
                node.transform.position=parent.TransformPoint(new Vector3((effect.x0+effect.x1)/2/ppu,-(effect.y1-(effect.y1-effect.y0)*.15f)/ppu,4));
                float target=(effect.y1-effect.y0)/ppu*1.15f,measured=ParticleHeight(node.GetComponent<ParticleSystem>(),2);
                Check(measured>0,spec.name,"effect_fire_emits",node.name);
                node.transform.localScale*=target/Mathf.Max(measured,.001f);
                float fitted=ParticleHeight(node.GetComponent<ParticleSystem>(),2);
                Check(Mathf.Abs(fitted/target-1)<.3f,spec.name,"effect_fire_fit",node.name+" target="+target+" fitted="+fitted);
            }
            else if(effect.kind=="smoke")node=GroundSmoke(cell,parent,root.transform);
            else throw new Exception("Unknown map effect: "+effect.kind);
            // Painter slot of the replaced art: after the layer below it, before the next (same order, nearer z).
            foreach(var r in node.GetComponentsInChildren<ParticleSystemRenderer>())
            {r.sortingLayerName="BGFront";r.sortingOrder=effect.kind=="smoke"?effect.order:effect.order-1;}
        }
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
        var art=AssetDatabase.LoadAssetAtPath<Texture2D>(input.effectArtDir+"/simpleSmoke2.png")??AssetDatabase.LoadAssetAtPath<Texture2D>(input.effectArtDir+"/Smoke26.png");
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
            else system.Simulate(system.name=="GroundSmoke"?system.main.startLifetime.constant*.8f:2,true,true);
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
                        var fires=cellRoot.GetComponentsInChildren<ParticleSystem>().Where(ps=>ps.name.StartsWith("TorchFire_")).ToArray();
                        Check(fires.Length==effectSpec.Count(e=>e.kind=="fire"),spec.name,"effect_fire_count",fires.Length.ToString());
                        foreach(var fire in fires)Check(fire.particleCount>0,spec.name,"effect_fire_live",fire.name);
                        var smoke=cellRoot.GetComponentsInChildren<ParticleSystem>().SingleOrDefault(ps=>ps.name=="GroundSmoke");
                        if(effectSpec.Any(e=>e.kind=="smoke"))Check(smoke!=null&&smoke.particleCount>10,spec.name,"effect_smoke_live",smoke==null?"missing":smoke.particleCount.ToString());
                        Capture(camera,spec.name+"-effects");
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
            if(effectBundle!=null){effectBundle.Unload(true);effectBundle=null;}
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
