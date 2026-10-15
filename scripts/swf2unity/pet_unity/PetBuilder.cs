using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEditor;
using UnityEditor.Animations;

public static class PetBuilder
{
    [Serializable] class Capture { public string file,state; public int ticks,width,height; public float[] origin; public float tpp; }
    [Serializable] class Report { public string status,bundle; public uint crc; public int states,images; public long textureBytes; public Capture[] captures; }
    static string output;
    public static void Build()
    {
        output=Environment.GetEnvironmentVariable("FLASH_PET_OUTPUT");
        if(string.IsNullOrEmpty(output)) throw new Exception("Output not supplied");
        Directory.CreateDirectory(output); Directory.CreateDirectory("Assets/Pet/Generated");
        PlayerSettings.colorSpace=ColorSpace.Gamma;
        var json=File.ReadAllText("Assets/Pet/pet.json");
        var data=JsonUtility.FromJson<FlashPetData>(json);
        var importer=(TextureImporter)AssetImporter.GetAtPath("Assets/Pet/atlas.png");
        importer.textureType=TextureImporterType.Default; importer.alphaSource=TextureImporterAlphaSource.FromInput;
        importer.alphaIsTransparency=false; importer.mipmapEnabled=false; importer.sRGBTexture=true;
        importer.npotScale=TextureImporterNPOTScale.None;
        importer.maxTextureSize=4096; importer.textureCompression=TextureImporterCompression.Uncompressed;
        importer.filterMode=FilterMode.Bilinear; importer.wrapMode=TextureWrapMode.Clamp; importer.isReadable=true;
        importer.SaveAndReimport();
        var texture=AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/Pet/atlas.png");
        if(texture.width!=data.atlasWidth || texture.height!=data.atlasHeight)throw new Exception("Atlas resized");
        var material=new Material(Shader.Find("Sprites/Default"));material.mainTexture=texture;
        AssetDatabase.CreateAsset(material,"Assets/Pet/Generated/Pet.mat");
        var root=new GameObject(data.name);root.AddComponent<SortingGroup>();
        var controller=AnimatorController.CreateAnimatorControllerAtPath("Assets/Pet/Generated/Pet.controller");
        var machine=controller.layers[0].stateMachine;
        foreach(var name in new[]{"Idle","Walk","Attack1","Attack2","Buff"})
        {
            var state=machine.AddState(name); if(name=="Idle") machine.defaultState=state;
        }
        root.AddComponent<Animator>().runtimeAnimatorController=controller;
        foreach(var pair in new[]{("Body",0),("Head",1),("Front",2)})
        {
            var child=new GameObject(pair.Item1);child.transform.SetParent(root.transform,false);
            var mesh=new Mesh();mesh.name=pair.Item1+"Quad";AssetDatabase.CreateAsset(mesh,"Assets/Pet/Generated/"+pair.Item1+".asset");
            child.AddComponent<MeshFilter>().sharedMesh=mesh;
            var renderer=child.AddComponent<MeshRenderer>();renderer.sharedMaterial=material;renderer.sortingOrder=pair.Item2;
        }
        var metadata=new GameObject("FlashPetData");metadata.transform.SetParent(root.transform,false);
        metadata.AddComponent<TextMesh>().text=json;metadata.SetActive(false);
        Apply(root,new FlashPetPlayback(data),true);
        PrefabUtility.SaveAsPrefabAsset(root,"Assets/Pet/Generated/"+data.name+".prefab");
        UnityEngine.Object.DestroyImmediate(root);AssetDatabase.SaveAssets();
        var nameBundle="daimyo_battlepet.unity3d";
        var build=new AssetBundleBuild{assetBundleName=nameBundle,assetNames=new[]{"Assets/Pet/Generated/"+data.name+".prefab"}};
        var manifest=BuildPipeline.BuildAssetBundles(output,new[]{build},BuildAssetBundleOptions.ChunkBasedCompression,BuildTarget.StandaloneWindows64);
        if(manifest==null)throw new Exception("Pet bundle build failed");
        var bundle=AssetBundle.LoadFromFile(Path.Combine(output,nameBundle));if(bundle==null)throw new Exception("Pet bundle reload failed");
        var prefab=bundle.LoadAsset<GameObject>(data.name);if(prefab==null)throw new Exception("Native prefab identity missing");
        if(prefab.GetComponentsInChildren<MonoBehaviour>(true).Length!=0)throw new Exception("Custom serialized script in pet bundle");
        var loadedJson=prefab.transform.Find("FlashPetData").GetComponent<TextMesh>().text;
        if(loadedJson!=json)throw new Exception("Pet program changed through bundle serialization");
        var loaded=JsonUtility.FromJson<FlashPetData>(loadedJson);
        var captures=new List<Capture>();var instance=UnityEngine.Object.Instantiate(prefab);
        foreach(var state in new[]{"Idle","Walk","Attack1","Attack2","Buff"})
        {
            var ticks=state=="Idle"?new[]{0,1,125,288,289,578}:state=="Walk"?new[]{0,5,11,12,125,126,251}:
                state=="Attack1"?new[]{0,2,3,16,26,27,28}:state=="Attack2"?new[]{0,2,3,12,17}:new[]{0,3,4,15,16,22};
            foreach(int t in ticks)
            {
                var clock=new FlashPetPlayback(loaded);clock.Enter(state);clock.Advance((double)t/24,state=="Walk");
                Apply(instance,clock);captures.Add(CaptureFrame(instance,clock,state,t));
            }
        }
        UnityEngine.Object.DestroyImmediate(instance);
        BuildPipeline.GetCRCForAssetBundle(Path.Combine(output,nameBundle),out uint crc);
        var report=new Report{status="passed",bundle=nameBundle,crc=crc,states=5,images=data.sprites.Length,
            textureBytes=(long)texture.width*texture.height*4,captures=captures.ToArray()};
        File.WriteAllText(Path.Combine(output,"native_pet_contract.json"),JsonUtility.ToJson(report,true));
        bundle.Unload(true);Debug.Log("FLASH_PET_BUNDLE_PASSED "+captures.Count);
    }
    static void Apply(GameObject root,FlashPetPlayback clock,bool shared=false)
    {
        var names=new[]{"Body","Head","Front"};var indices=new[]{clock.Back,clock.Head,clock.Front};
        for(int i=0;i<3;i++)
        {
            var child=root.transform.Find(names[i]);var renderer=child.GetComponent<MeshRenderer>();renderer.enabled=indices[i]>=0;
            if(indices[i]<0)continue;
            var s=clock.Data.sprites[indices[i]];var r=s.rect;var m=i==1?clock.HeadMatrix:null;
            float x=s.origin[0],y=s.origin[1],w=r[2]*s.tpp,h=r[3]*s.tpp;
            var points=new[]{new Vector2(x,y),new Vector2(x+w,y),new Vector2(x+w,y+h),new Vector2(x,y+h)};
            var vertices=new Vector3[4];
            for(int j=0;j<4;j++)
            {
                var p=points[j];if(m!=null)p=new Vector2(m[0]*p.x+m[2]*p.y+m[4],m[1]*p.x+m[3]*p.y+m[5]);
                vertices[j]=new Vector3(p.x*clock.Data.twipToUnit,-p.y*clock.Data.twipToUnit,0);
            }
            var filter=child.GetComponent<MeshFilter>();var mesh=shared?filter.sharedMesh:filter.mesh;mesh.vertices=vertices;
            float u=r[0]/clock.Data.atlasWidth,v=r[1]/clock.Data.atlasHeight,uw=r[2]/clock.Data.atlasWidth,vh=r[3]/clock.Data.atlasHeight;
            mesh.uv=new[]{new Vector2(u,v+vh),new Vector2(u+uw,v+vh),new Vector2(u+uw,v),new Vector2(u,v)};
            mesh.triangles=new[]{0,1,2,0,2,3};mesh.colors=new[]{Color.white,Color.white,Color.white,Color.white};mesh.RecalculateBounds();
        }
    }
    static Capture CaptureFrame(GameObject root,FlashPetPlayback clock,string state,int ticks)
    {
        var active=root.GetComponentsInChildren<MeshRenderer>().Where(r=>r.enabled).ToArray();
        var bounds=active[0].bounds;foreach(var r in active.Skip(1))bounds.Encapsulate(r.bounds);
        float tpp=10,unit=clock.Data.twipToUnit;
        float left=Mathf.Floor(bounds.min.x/unit/tpp)*tpp-40,top=Mathf.Floor(-bounds.max.y/unit/tpp)*tpp-40;
        int width=Mathf.CeilToInt((bounds.max.x/unit-left)/tpp)+4,height=Mathf.CeilToInt((-bounds.min.y/unit-top)/tpp)+4;
        var cameraGO=new GameObject("CaptureCamera");var camera=cameraGO.AddComponent<Camera>();
        camera.orthographic=true;camera.orthographicSize=height*tpp*unit/2;camera.aspect=(float)width/height;
        camera.transform.position=new Vector3((left+width*tpp/2)*unit,-(top+height*tpp/2)*unit,-10);
        camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=Color.clear;camera.nearClipPlane=.1f;camera.farClipPlane=100;
        var target=new RenderTexture(width,height,24,RenderTextureFormat.ARGB32);camera.targetTexture=target;camera.Render();
        var previous=RenderTexture.active;RenderTexture.active=target;
        var image=new Texture2D(width,height,TextureFormat.RGBA32,false);image.ReadPixels(new Rect(0,0,width,height),0,0);image.Apply();
        string file=state+"_"+ticks+".png";File.WriteAllBytes(Path.Combine(output,file),image.EncodeToPNG());
        RenderTexture.active=previous;camera.targetTexture=null;UnityEngine.Object.DestroyImmediate(image);UnityEngine.Object.DestroyImmediate(target);UnityEngine.Object.DestroyImmediate(cameraGO);
        return new Capture{file=file,state=state,ticks=ticks,width=width,height=height,origin=new[]{left,top},tpp=tpp};
    }
}
