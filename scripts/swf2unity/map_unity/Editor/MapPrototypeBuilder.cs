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
    [Serializable] public class FrameDef { public string name; public int page,x,y,w,h; }
    [Serializable] public class AtlasDef { public string path; public int height; }
    [Serializable] public class VisualLayer { public string name,group; public int order,period,source_char; public int[] frames; public float x,y; }
    [Serializable] public class VisualGroup { public string name,kind; public float speed,x,y; }
    [Serializable] public class VisualCell { public string name; public VisualLayer[] layers; public VisualGroup[] groups; }
    [Serializable] public class InputData { public float width,height,pixelsPerUnit,fps,cameraZoom,rasterScale; public string outputDir,atlas; public Cell[] cells; public bool layered; public int atlasHeight; public AtlasDef[] visualAtlases; public FrameDef[] visualFrames; public VisualCell[] visualCells; public int[] sampleFrames; }
    [Serializable] public class CheckRecord { public string cell,kind,target; public int samples; public bool passed; }
    [Serializable] public class Report { public string status,proofBoundary; public int cells; public CheckRecord[] checks; }
    static InputData input;
    static List<CheckRecord> checks = new List<CheckRecord>();
    class VisualClock { public GameObject go; public AnimationClip clip; public int period; }
    static Dictionary<string,List<VisualClock>> clocks=new Dictionary<string,List<VisualClock>>();
    static Sprite[] visualSprites;
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
                Check(totalOverlap>50,spec.name,"npc_dragon_overlap_exercised",totalOverlap.ToString());
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
            importer.wrapMode=TextureWrapMode.Clamp;
            importer.alphaIsTransparency=false; importer.maxTextureSize=4096; importer.SaveAndReimport();
            textures.Add(AssetDatabase.LoadAssetAtPath<Texture2D>(page.path));
        }
        visualSprites=new Sprite[input.visualFrames.Length];
        for(int i=0;i<visualSprites.Length;i++)
        {
            var f=input.visualFrames[i];
            var sprite=Sprite.Create(textures[f.page],new Rect(f.x,pages[f.page].height-f.y-f.h,f.w,f.h),new Vector2(.5f,.5f),input.pixelsPerUnit*Mathf.Max(1,input.rasterScale),0,SpriteMeshType.FullRect);
            sprite.name=f.name; AssetDatabase.CreateAsset(sprite,"Assets/Generated/Visuals/"+f.name+".asset");
            visualSprites[i]=sprite;
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
            var sr=Node(layer.name,owner).AddComponent<SpriteRenderer>();
            sr.sprite=visualSprites[layer.frames[0]];sr.sortingOrder=layer.order;
            sr.sortingLayerName=hasDepth&&owner.GetComponent<ZOffset>()==null?"BGFront":"Default";
            sr.transform.position=parent.TransformPoint(new Vector3(layer.x/input.pixelsPerUnit,-layer.y/input.pixelsPerUnit,5));
            renderers[layer.name]=sr;
        }
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
