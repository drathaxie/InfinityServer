public static class NodeAnimationCancel
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		caster?.animation?.ForceCancelCurrentAnimation();
	}
}

public static class NodeAnimationHitbox
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.inSameFrame() && !caster.isMainPlayer && !(caster.animation == null) && !(caster.animation.animator == null))
		{
			string anim = props["Animation"].Value<string>();
			float speed = ((props.SelectToken("Speed") != null) ? props["Speed"].Value<float>() : 1f);
			caster.animation.Play(new HitboxRegisterAnimation(anim, speed));
		}
	}

	public static List<string> Input(Entity caster, JObject props)
	{
		float x = props["X"].Value<float>();
		float y = props["Y"].Value<float>();
		float width = props["Width"].Value<float>();
		float height = props["Height"].Value<float>();
		string text = props["Animation"].Value<string>();
		float speed = ((props.SelectToken("Speed") != null) ? props["Speed"].Value<float>() : 1f);
		BoxTime boxTime = new BoxTime
		{
			animationKey = text,
			time = props["Time"].Value<float>(),
			X = x,
			Y = y,
			Width = width,
			Height = height,
			Slot = props["slot"].Value<string>(),
			ContextId = (props["contextId"]?.Value<string>() ?? "")
		};
		caster.animation.queuedBox[text] = boxTime;
		caster.animation.queuedAnimations.Enqueue(new HitboxRegisterAnimation(text, speed)
		{
			Slot = boxTime.Slot,
			ContextId = boxTime.ContextId
		});
		return new List<string>();
	}
}

public static class NodeAura
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.inSameFrame())
		{
			return;
		}
		string text = props["AuraName"].Value<string>();
		List<string> list = props["Targets"].Select((JToken x) => (string?)x).ToList();
		bool flag = props["Hide"].Value<bool>();
		string value = ((props.SelectToken("Animation") != null) ? props["Animation"].Value<string>() : "");
		byte uniquenessType = (byte)(props.SelectToken("uniquenessType")?.Value<int>() ?? 0);
		string casterTS = props.SelectToken("casterTS")?.Value<string>() ?? string.Empty;
		for (int num = 0; num < list.Count; num++)
		{
			if (!caster.hasDamageQueue(instanceID) || !caster.updateDamageQueueAura(instanceID, list[num], text, flag, uniquenessType, casterTS))
			{
				if (!string.IsNullOrEmpty(value))
				{
					caster.animation.GetOrCreateAuraQueue(instanceID).Enqueue(new AuraTime
					{
						name = text,
						hidden = flag,
						targetString = list[num],
						uniquenessType = uniquenessType,
						casterTS = casterTS
					});
				}
				else
				{
					HUDCanvas.NotifyAura(new ResponseAuraChange
					{
						Target = list[num],
						auraCmd = ResponseAuraChange.auraAction.Add,
						nam = text,
						uniquenessType = uniquenessType,
						casterTS = casterTS
					}, caster, flag);
				}
			}
		}
	}
}

public static class NodeAuraVFX
{
	private static Dictionary<(Entity, string), GameObject> activeVFX = new Dictionary<(Entity, string), GameObject>();

	public static void ClearForEntity(Entity e)
	{
		if (e == null)
		{
			return;
		}
		List<(Entity, string)> list = new List<(Entity, string)>();
		foreach (KeyValuePair<(Entity, string), GameObject> item in activeVFX)
		{
			if (item.Key.Item1 == e)
			{
				list.Add(item.Key);
			}
		}
		foreach (var item2 in list)
		{
			if (activeVFX.TryGetValue(item2, out var value) && value != null)
			{
				UnityEngine.Object.Destroy(value);
			}
			activeVFX.Remove(item2);
		}
	}

	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		string text = props["AuraName"].Value<string>();
		string text2 = props["VFX"].Value<string>();
		AssetBundleData bundle = (caster as Player)?.classBundle;
		Singleton<ParticlesManager>.Instance.QueueClassParticle(text2 + "_Appear", bundle);
		Singleton<ParticlesManager>.Instance.QueueClassParticle(text2 + "_Exit", bundle);
		caster.AddAuraVFX(text, text2);
		foreach (KeyValuePair<string, bool> aura in caster.auras)
		{
			if (aura.Key == text)
			{
				HandleVFX(caster, text, delete: false);
				break;
			}
		}
	}

	public static void HandleVFX(Entity caster, string auraName, bool delete)
	{
		GameObject gameObject = caster.getGameObject();
		if (gameObject == null)
		{
			if (delete)
			{
				caster.PendAuraVFX(auraName);
			}
			return;
		}
		if (!gameObject.activeSelf && delete)
		{
			caster.PendAuraVFX(auraName);
			return;
		}
		string auraVFX = caster.GetAuraVFX(auraName, delete);
		if (auraVFX == null)
		{
			return;
		}
		(Entity, string) key = (caster, auraName);
		GameObject gameObject2 = (delete ? Singleton<ParticlesManager>.Instance.GetParticle(auraVFX + "_Exit") : Singleton<ParticlesManager>.Instance.GetParticle(auraVFX + "_Appear"));
		if (gameObject2 == null)
		{
			return;
		}
		Transform transform = Util.RecursiveFindChild(gameObject.transform, "VFX");
		Transform parent = ((transform != null) ? transform : gameObject.transform);
		GameObject gameObject3 = UnityEngine.Object.Instantiate(gameObject2, parent);
		gameObject3.transform.localPosition = Vector3.zero;
		gameObject3.SetActive(value: true);
		if (delete)
		{
			if (activeVFX.TryGetValue(key, out var value))
			{
				if (value != null)
				{
					UnityEngine.Object.Destroy(value);
				}
				activeVFX.Remove(key);
			}
			TimedKill timedKill = gameObject3.GetComponent<TimedKill>();
			if (timedKill == null)
			{
				timedKill = gameObject3.AddComponent<TimedKill>();
			}
			timedKill.seconds = 7f;
		}
		else
		{
			if (activeVFX.TryGetValue(key, out var value2) && value2 != null)
			{
				UnityEngine.Object.Destroy(value2);
			}
			activeVFX[key] = gameObject3;
		}
	}
}

public static class NodeChannel
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer)
		{
			Singleton<Combat>.Instance.startSkillStream();
		}
	}
}

public static class NodeConditionalRange
{
	public static List<string> Input(Entity caster, JObject props)
	{
		float hRange = props["hrange"].Value<float>();
		float vRange = props["vrange"].Value<float>();
		props["type"].Value<string>();
		Entity target = Entity.mainPlayer.target;
		if (target == null || target.currentState == Entity.State.Dead)
		{
			return new List<string> { "false" };
		}
		bool flag = Singleton<Combat>.Instance.isTargetInRange(caster, target, hRange, vRange);
		return new List<string> { flag ? "true" : "false" };
	}
}

public static class NodeCooldown
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.isMainPlayer)
		{
			return;
		}
		int num = props["Slot"].Value<int>();
		int num2 = props["CD"].Value<int>();
		SkillSlotButton slot = Singleton<UISkillSlots>.Instance.GetSlot(num);
		if (!(slot == null))
		{
			string text = ((props.SelectToken("Animation") != null) ? props["Animation"].Value<string>() : "");
			if (string.IsNullOrEmpty(text))
			{
				slot.pendingCooldown = false;
				slot.showCooldown(num2);
				return;
			}
			slot.pendingCooldown = true;
			caster.animation.queuedCooldown[text] = new CDTime
			{
				slot = num,
				cd = num2
			};
		}
	}
}

public static class NodeDamage
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		List<int> list = props["DamageTypes"].Select((JToken x) => (int)x).ToList();
		List<string> list2 = props["Targets"].Select((JToken x) => (string?)x).ToList();
		List<int> list3 = props["Damages"].Select((JToken x) => (int)x).ToList();
		List<int> list4 = props["TargetHPs"].Select((JToken x) => (int)x).ToList();
		bool flag = props.SelectToken("Immediate")?.Value<bool>() ?? false;
		int? num = null;
		if (caster.isMainPlayer)
		{
			for (int num2 = 0; num2 < list2.Count; num2++)
			{
				Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(list2[num2]);
				if (!flag && !num.HasValue)
				{
					num = caster.getDamageQueueIndex(instanceID);
				}
				if (entityByTargetString != null && (!entityByTargetString.isPlayer || entityByTargetString.isMainPlayer))
				{
					entityByTargetString.damageTickets++;
				}
				if (flag || caster.hasDied || caster.currentState == Entity.State.Dead)
				{
					if (entityByTargetString != null)
					{
						Singleton<Combat>.Instance.PlayHitSound(caster, entityByTargetString, (BattleTextBouncer.DamageType)list[num2]);
						BattleTextBouncer.Dispense(caster, entityByTargetString, list3[num2], list4[num2], (BattleTextBouncer.DamageType)list[num2]);
					}
				}
				else
				{
					caster.addDamageQueue(instanceID, num.Value, new DamageTicket
					{
						targetString = list2[num2],
						targetHP = list4[num2],
						damage = list3[num2],
						dType = (BattleTextBouncer.DamageType)list[num2]
					});
				}
				if (entityByTargetString != null)
				{
					if (entityByTargetString.walk != null)
					{
						entityByTargetString.walk.targetPlayer = caster;
					}
					if (list2[num2][0] == 'm' && caster.target == null)
					{
						caster.setTarget(entityByTargetString);
					}
				}
			}
		}
		else
		{
			if (caster is Monster monster)
			{
				monster.allowHitInterrupt = true;
			}
			for (int num3 = 0; num3 < list2.Count; num3++)
			{
				Entity entityByTargetString2 = Area.currentArea.GetEntityByTargetString(list2[num3]);
				if (entityByTargetString2 == null)
				{
					continue;
				}
				if (entityByTargetString2 is Monster && entityByTargetString2.walk != null)
				{
					entityByTargetString2.walk.targetPlayer = caster;
				}
				if (!caster.isPlayer)
				{
					if (entityByTargetString2.isMainPlayer)
					{
						Entity.mainPlayer.damageTickets++;
						if (flag || caster.hasDied || caster.currentState == Entity.State.Dead)
						{
							Singleton<Combat>.Instance.PlayHitSound(caster, entityByTargetString2, (BattleTextBouncer.DamageType)list[num3]);
							BattleTextBouncer.Dispense(caster, entityByTargetString2, list3[num3], list4[num3], (BattleTextBouncer.DamageType)list[num3]);
						}
						else
						{
							if (!flag && !num.HasValue)
							{
								num = caster.getDamageQueueIndex(instanceID);
							}
							caster.addDamageQueue(instanceID, num.Value, new DamageTicket
							{
								targetString = list2[num3],
								targetHP = list4[num3],
								damage = list3[num3],
								dType = (BattleTextBouncer.DamageType)list[num3]
							});
						}
					}
					else
					{
						entityByTargetString2.HP = list4[num3];
					}
					if (caster.Frame == Entity.mainPlayer.Frame)
					{
						if (caster.animation != null && caster.animation.walk != null && !(caster as Monster).NoTurn)
						{
							caster.animation.walk.LookAt(entityByTargetString2.getGameObject().transform.localPosition.x);
						}
						caster.setTarget(entityByTargetString2);
						if (entityByTargetString2.isCharging() || (caster as Monster).NoMove || (Singleton<Combat>.Instance != null && Singleton<Combat>.Instance.mapHasGeometry))
						{
							return;
						}
						caster.Charge();
					}
				}
				else
				{
					entityByTargetString2.HP = list4[num3];
				}
			}
		}
		caster.checkDamageQueue(instanceID);
	}

	public static void ForceDispense(Entity caster, int instanceID)
	{
		while (true)
		{
			List<DamageTicket> damageQueue = caster.getDamageQueue(instanceID);
			if (damageQueue == null)
			{
				break;
			}
			for (int i = 0; i < damageQueue.Count; i++)
			{
				try
				{
					Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(damageQueue[i].targetString);
					if (entityByTargetString == null)
					{
						UnityEngine.Debug.LogError("[DAMAGE] ForceDispense: target " + damageQueue[i].targetString + " not found, skipping");
						continue;
					}
					if (damageQueue[i].auraName != null)
					{
						foreach (KeyValuePair<string, AuraNotifyMeta> item in damageQueue[i].auraName)
						{
							HUDCanvas.NotifyAura(new ResponseAuraChange
							{
								Target = entityByTargetString.TargetString,
								auraCmd = ResponseAuraChange.auraAction.Add,
								nam = item.Key,
								uniquenessType = item.Value.uniquenessType,
								casterTS = item.Value.casterTS
							}, caster, item.Value.hide);
						}
					}
					BattleTextBouncer.Dispense(caster, entityByTargetString, damageQueue[i].damage, damageQueue[i].targetHP, damageQueue[i].dType);
				}
				catch (Exception ex)
				{
					UnityEngine.Debug.LogError($"[DAMAGE] ForceDispense failed for {caster?.TargetString} ticket {i} (target {damageQueue[i].targetString}): {ex.Message}");
				}
			}
		}
	}
}

public static class NodeDash
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.isMainPlayer && !(caster.Frame != Entity.mainPlayer.Frame))
		{
			int dur = ((props.SelectToken("Duration") == null) ? 400 : props["Duration"].Value<int>());
			float xoffset = ((props.SelectToken("OffsetX") == null) ? 0f : props["OffsetX"].Value<float>());
			CombatMovement.BeginDash(caster, "0", "", xoffset, dur, sendGai: false);
		}
	}

	public static List<string> Input(Entity caster, JObject props)
	{
		int Duration = ((props.SelectToken("Duration") == null) ? 400 : props["Duration"].Value<int>());
		string Slot = props["slot"].Value<string>();
		string ContextId = props["contextId"]?.Value<string>() ?? "";
		string Animation = ((props.SelectToken("Animation") == null) ? "None" : props["Animation"].Value<string>());
		if (Entity.mainPlayer.animation.currentAnimation != null && Entity.mainPlayer.animation.currentAnimation.animationState == Animation)
		{
			Animation = "None";
		}
		if (Animation != "None")
		{
			Entity.mainPlayer.SetDashHandler(delegate(string anim)
			{
				if (anim == Animation)
				{
					CombatMovement.BeginDash(caster, Slot, ContextId, props["OffsetX"].Value<float>(), Duration);
					Entity.mainPlayer.SetDashHandler(null);
				}
			});
		}
		else
		{
			CombatMovement.BeginDash(caster, Slot, ContextId, props["OffsetX"].Value<float>(), Duration);
		}
		return new List<string>();
	}
}

public static class NodeDashToTarget
{
	private const float SkipVTolerance = 0.5f;

	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!(caster.Frame != Entity.mainPlayer.Frame))
		{
			bool flag = props.SelectToken("Async") != null && props["Async"].Value<bool>();
			if (!caster.isMainPlayer || flag)
			{
				StartDash(caster, props, sendGai: false);
			}
		}
	}

	public static List<string> Input(Entity caster, JObject props)
	{
		return StartDash(caster, props);
	}

	public static List<string> StartDash(Entity caster, JObject props, bool sendGai = true)
	{
		int Duration = props["Duration"].Value<int>();
		string Slot = ((sendGai && props.SelectToken("slot") != null) ? props["slot"].Value<string>() : "0");
		string ContextId = ((!sendGai) ? "" : (props["contextId"]?.Value<string>() ?? ""));
		string ts = props["Target"].Value<string>();
		Entity eTarget = Area.currentArea.GetEntityByTargetString(ts);
		if (eTarget == null)
		{
			return new List<string>();
		}
		bool FaceTarget = props.SelectToken("Face") != null && props["Face"].Value<bool>();
		string Animation = ((props.SelectToken("Animation") == null) ? "None" : props["Animation"].Value<string>());
		bool num = props.SelectToken("forceMovement") == null || props["forceMovement"].Value<bool>();
		float offsetX = props["OffsetX"].Value<float>();
		if (!num && Singleton<Combat>.Instance.isTargetInRange(caster, eTarget, offsetX, 0.5f))
		{
			return new List<string>();
		}
		if (Animation != "None" && caster.animation != null && caster.animation.currentAnimation != null && caster.animation.currentAnimation.animationState == Animation)
		{
			Animation = "None";
		}
		if (Animation != "None" && caster.animation != null)
		{
			Action<string> handler = null;
			handler = delegate(string anim)
			{
				if (anim == Animation)
				{
					CombatMovement.BeginDashToTarget(caster, Slot, ContextId, eTarget, offsetX, Duration, FaceTarget, sendGai);
					EntityAnimationControl animation2 = caster.animation;
					animation2.OnAnimationPlayed = (Action<string>)Delegate.Remove(animation2.OnAnimationPlayed, handler);
				}
			};
			EntityAnimationControl animation = caster.animation;
			animation.OnAnimationPlayed = (Action<string>)Delegate.Combine(animation.OnAnimationPlayed, handler);
		}
		else
		{
			CombatMovement.BeginDashToTarget(caster, Slot, ContextId, eTarget, offsetX, Duration, FaceTarget, sendGai);
		}
		return new List<string>();
	}
}

public static class NodeDisableSkill
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer)
		{
			int slotNum = props["Slot"].Value<int>();
			bool ds = props["Disabled"].Value<bool>();
			SkillSlotButton slot = Singleton<UISkillSlots>.Instance.GetSlot(slotNum);
			if (!(slot == null))
			{
				slot.disableSkill(ds);
			}
		}
	}
}

public static class NodeDispenseDamage
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.isMainPlayer)
		{
			return;
		}
		List<DamageTicket> damageQueue = caster.getDamageQueue(instanceID);
		if (damageQueue == null)
		{
			return;
		}
		for (int i = 0; i < damageQueue.Count; i++)
		{
			Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(damageQueue[i].targetString);
			if (entityByTargetString == null)
			{
				UnityEngine.Debug.LogError("[DAMAGE] NodeDispenseDamage: target " + damageQueue[i].targetString + " not found, skipping");
				continue;
			}
			if (entityByTargetString.isMainPlayer)
			{
				entityByTargetString.damageTickets++;
			}
			Singleton<Combat>.Instance.PlayHitSound(caster, entityByTargetString, damageQueue[i].dType);
			BattleTextBouncer.Dispense(caster, entityByTargetString, damageQueue[i].damage, damageQueue[i].targetHP, damageQueue[i].dType);
		}
	}
}

public static class NodeGlobalCooldown
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.isMainPlayer)
		{
			return;
		}
		SkillSlotButton[] slots = Singleton<UISkillSlots>.Instance.GetSlots();
		int num = -1;
		List<int> list = props["CD"].Select((JToken x) => (int)x).ToList();
		SkillSlotButton[] array = slots;
		foreach (SkillSlotButton skillSlotButton in array)
		{
			num++;
			if (num < list.Count)
			{
				if (list[num] >= 0)
				{
					skillSlotButton.showCooldown(list[num]);
				}
				continue;
			}
			break;
		}
	}
}

public static class NodeHit
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer || (caster is Monster && caster.inSameFrame() && !(caster.animation == null)))
		{
			BaseTime baseTime = new BaseTime
			{
				animationKey = props["Animation"].Value<string>(),
				time = props["Time"].Value<float>()
			};
			if (!caster.animation.queuedHit.ContainsKey(baseTime.animationKey))
			{
				caster.animation.queuedHit[baseTime.animationKey] = new List<BaseTime>();
			}
			caster.animation.queuedHit[baseTime.animationKey].Add(baseTime);
		}
	}
}

public static class NodeHitStream
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!(caster.Frame != Entity.mainPlayer.Frame))
		{
			string anim = props["CastAnimation"]?.Value<string>() ?? "Castcharge";
			if (caster.currentState != Entity.State.Dead)
			{
				caster.animation.Play(new InterruptAllPriorityAnimation(anim, 1f));
			}
			SingleTileCollider.TileFillType fillType = SingleTileCollider.TileFillType.Rectangle;
			HotTile hotTile = Entity.mainPlayer.getGameObject().AddComponent<HotTile>();
			hotTile.CasterString = caster.TargetString;
			if (props.SelectToken("VFX") != null)
			{
				hotTile.VFX = props["VFX"].Value<string>();
			}
			hotTile.DuringAnimation = props["DuringAnimation"]?.Value<string>();
			hotTile.CompletedAnimation = props["CompletedAnimation"]?.Value<string>();
			hotTile.FinishAnimation = props["FinishAnimation"]?.Value<string>();
			hotTile.TS = props["Time"].Value<long>();
			hotTile.Duration = props["Duration"].Value<int>();
			hotTile.Setup(caster, fillType, new Vector2(props["PosX"].Value<float>(), props["PosY"].Value<float>()), props["Speed"].Value<float>(), new Vector2(props["ScaleX"].Value<float>(), props["ScaleY"].Value<float>()));
		}
	}
}

public static class NodeHitTiles
{
	public static List<string> MonsterInput(string MonsterString, JObject response, long responseTS)
	{
		Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(MonsterString);
		if (entityByTargetString == null || entityByTargetString.Frame != Entity.mainPlayer.Frame)
		{
			return new List<string>();
		}
		string anim = response["CastAnimation"]?.Value<string>() ?? "Castcharge";
		if (entityByTargetString.currentState != Entity.State.Dead)
		{
			entityByTargetString.animation.Play(new InterruptAllPriorityAnimation(anim, 1f));
		}
		string text = response["Shape"].Value<string>();
		SingleTileCollider.TileFillType fillType = SingleTileCollider.TileFillType.Circle;
		if (!(text == "Rectangle"))
		{
			if (text == "VerticalRectangle")
			{
				fillType = SingleTileCollider.TileFillType.VerticalRectangle;
			}
		}
		else
		{
			fillType = SingleTileCollider.TileFillType.Rectangle;
		}
		SingleTile singleTile = Entity.mainPlayer.getGameObject().AddComponent<SingleTile>();
		if (response.SelectToken("VFX") != null)
		{
			singleTile.VFX = response["VFX"].Value<string>();
		}
		singleTile.MonsterString = MonsterString;
		singleTile.FinishAnimation = response["FinishAnimation"]?.Value<string>();
		singleTile.OnFinish += OnFinish;
		singleTile.Setup(Entity.mainPlayer, fillType, response["Speed"].Value<float>(), new Vector2(response["ScaleX"].Value<float>(), response["ScaleY"].Value<float>()));
		return new List<string>();
	}

	public static void OnFinish(string MonsterString, bool success)
	{
		GeometryTileReport.Report(MonsterString, "HitTiles", success);
	}
}

public static class NodeHitbox
{
	public const float EdgePad = 0.1f;

	public static void Execute(Entity caster, int instanceID, JObject props)
	{
	}

	public static List<string> Input(Entity caster, JObject props)
	{
		float x = props["X"]?.Value<float>() ?? 0f;
		float y = props["Y"]?.Value<float>() ?? 0f;
		float width = props["Width"]?.Value<float>() ?? 1f;
		float height = props["Height"]?.Value<float>() ?? 1f;
		Entity entity = null;
		string text = props["OriginTarget"]?.Value<string>();
		if (!string.IsNullOrEmpty(text))
		{
			entity = Area.currentArea.GetEntityByTargetString(text);
			if (entity == null)
			{
				return new List<string>();
			}
		}
		RaycastHit2D[] array = SpawnHitBox(x, y, width, height, entity);
		List<string> list = new List<string>();
		RaycastHit2D[] array2 = array;
		for (int i = 0; i < array2.Length; i++)
		{
			string text2 = ResolveFootprintTarget(array2[i], caster);
			if (text2 != null)
			{
				list.Add(text2);
			}
		}
		Entity entity2 = entity ?? Entity.mainPlayer;
		if (Singleton<Combat>.Instance != null && Singleton<Combat>.Instance.mapHasGeometry && entity2 != null && GetServerBoxGeometry(x, y, width, height, entity2, out var offX, out var offY, out var bhw, out var bhh))
		{
			list.Add("hb");
			list.Add(entity2.TargetString);
			list.Add(offX.ToString(CultureInfo.InvariantCulture));
			list.Add(offY.ToString(CultureInfo.InvariantCulture));
			list.Add(bhw.ToString(CultureInfo.InvariantCulture));
			list.Add(bhh.ToString(CultureInfo.InvariantCulture));
		}
		return list;
	}

	public static string ResolveFootprintTarget(RaycastHit2D hit, Entity caster = null)
	{
		if (hit.collider == null || hit.collider.gameObject == null)
		{
			return null;
		}
		if (hit.collider.gameObject.name != "FootprintRing")
		{
			return null;
		}
		Transform parent = hit.collider.gameObject.transform.parent;
		if (parent == null)
		{
			return null;
		}
		if (caster != null && parent.gameObject == caster.getGameObject())
		{
			return null;
		}
		string name = parent.name;
		if (!name.StartsWith("m:") && !name.StartsWith("p:"))
		{
			return null;
		}
		return name;
	}

	public static bool GetServerBoxGeometry(float X, float Y, float Width, float Height, Entity anchor, out float offX, out float offY, out float bhw, out float bhh)
	{
		offX = (offY = (bhw = (bhh = 0f)));
		GameObject gameObject = anchor?.getGameObject();
		GameObject gameObject2 = Singleton<Game>.Instance?.getEntityContainer();
		if (gameObject == null || gameObject2 == null)
		{
			return false;
		}
		Transform transform = gameObject2.transform;
		X = anchor.ScaleToEntity(X);
		Y = anchor.ScaleToEntity(Y, isX: false);
		Width = anchor.ScaleToEntity(Width);
		Height = anchor.ScaleToEntity(Height, isX: false);
		if (gameObject.transform.localScale.x < 0f)
		{
			X *= -1f;
		}
		Vector3 vector = new Vector3(gameObject.transform.position.x + X, gameObject.transform.position.y + Y, 0f);
		float x = (float)Math.Round(Width) * 0.5f + anchor.ScaleToEntity(0.1f);
		float y = (float)Math.Round(Height) * 0.5f + anchor.ScaleToEntity(0.1f, isX: false);
		Vector3 vector2 = transform.InverseTransformPoint(vector);
		Vector3 vector3 = transform.InverseTransformPoint(vector + new Vector3(x, 0f, 0f));
		Vector3 vector4 = transform.InverseTransformPoint(vector - new Vector3(x, 0f, 0f));
		Vector3 vector5 = transform.InverseTransformPoint(vector + new Vector3(0f, y, 0f));
		Vector3 vector6 = transform.InverseTransformPoint(vector - new Vector3(0f, y, 0f));
		Vector3 footprintLocalPosition = anchor.GetFootprintLocalPosition();
		offX = vector2.x - footprintLocalPosition.x;
		offY = vector2.y - footprintLocalPosition.y;
		bhw = Mathf.Abs(vector3.x - vector4.x) * 0.5f;
		bhh = Mathf.Abs(vector5.y - vector6.y) * 0.5f;
		return true;
	}

	public static RaycastHit2D[] SpawnHitBox(float X, float Y, float Width, float Height, Entity origin = null)
	{
		Entity entity = origin ?? Entity.mainPlayer;
		if (entity == null)
		{
			return new RaycastHit2D[0];
		}
		GameObject gameObject = entity.getGameObject();
		if (gameObject == null)
		{
			return new RaycastHit2D[0];
		}
		X = entity.ScaleToEntity(X);
		Y = entity.ScaleToEntity(Y, isX: false);
		Width = entity.ScaleToEntity(Width);
		Height = entity.ScaleToEntity(Height, isX: false);
		if (gameObject.transform.localScale.x < 0f)
		{
			X *= -1f;
		}
		Vector3 vector = new Vector3(gameObject.transform.position.x + X, gameObject.transform.position.y + Y, 0f);
		float num = entity.ScaleToEntity(0.1f);
		float num2 = entity.ScaleToEntity(0.1f, isX: false);
		Vector2 size = new Vector2((float)Math.Round(Width) + num * 2f, (float)Math.Round(Height) + num2 * 2f);
		if (Singleton<Combat>.Instance.showHitboxes)
		{
			GameObject gameObject2 = new GameObject("VisualShape");
			zgizmoTest obj = gameObject2.AddComponent<zgizmoTest>();
			obj.center = vector;
			obj.size = size;
			UnityEngine.Object.Destroy(gameObject2, 3f);
		}
		float distance = 0.01f;
		Vector2 zero = Vector2.zero;
		return Physics2D.BoxCastAll(vector, size, 0f, zero, distance, 512);
	}
}

public static class NodeImpactAura
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.inSameFrame())
		{
			string text = props["SpellImpact"].Value<string>();
			Singleton<ParticlesManager>.Instance.QueueClassParticle(text, (caster as Player)?.classBundle);
			ImpactAura impactAura = new ImpactAura
			{
				auraKey = props["AuraName"].Value<string>(),
				impact = text
			};
			caster.animation.queuedImpactAura[impactAura.auraKey] = impactAura;
		}
	}
}

public static class NodeImpactSoundFX
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.isMainPlayer)
		{
			return;
		}
		string text = props["Sound"].Value<string>();
		if (text.Contains(","))
		{
			string[] array = text.Split(",");
			text = array[Game.seed.Next(0, array.Length)];
		}
		float minPitch = ((props.SelectToken("MinPitch") != null) ? props["MinPitch"].Value<float>() : (-1f));
		float maxPitch = ((props.SelectToken("MaxPitch") != null) ? props["MaxPitch"].Value<float>() : (-1f));
		string text2 = props["Animation"].Value<string>();
		ImpactSound impactSound = new ImpactSound
		{
			sound = text,
			animationKey = text2,
			minPitch = minPitch,
			maxPitch = maxPitch
		};
		if (text2.Contains(","))
		{
			string[] array2 = text2.Split(",");
			for (int i = 0; i < array2.Length; i++)
			{
				caster.animation.queuedImpactSound[array2[i]] = impactSound;
			}
		}
		else
		{
			caster.animation.queuedImpactSound[impactSound.animationKey] = impactSound;
		}
	}
}

public static class NodeIndexReset
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.isMainPlayer)
		{
			return;
		}
		int num = props["Slot"].Value<int>();
		int duration = props["Time"].Value<int>();
		int cD = ((props.SelectToken("CD") != null) ? props["CD"].Value<int>() : 0);
		string icon = ((props.SelectToken("Icon") != null) ? props["Icon"].Value<string>() : "");
		bool shared = props.SelectToken("Shared") != null && props["Shared"].Value<bool>();
		bool stay = props.SelectToken("Stay") != null && props["Stay"].Value<bool>();
		long? num2 = ((props.SelectToken("TS") != null) ? props["TS"].Value<long?>() : ((long?)null));
		SkillSlotButton slot = Singleton<UISkillSlots>.Instance.GetSlot(num);
		if (!(slot == null) && !(slot.resetRing == null))
		{
			IndexSkillResetRing resetRing = slot.resetRing;
			GameObject gameObject = resetRing.gameObject;
			resetRing.SetDuration(duration);
			resetRing.SetSlot(num);
			resetRing.SetCD(cD);
			resetRing.SetIcon(icon);
			resetRing.SetShared(shared);
			resetRing.SetStay(stay);
			if (gameObject.activeSelf)
			{
				resetRing.Refresh();
			}
			else
			{
				gameObject.SetActive(value: true);
			}
			if (num2.HasValue)
			{
				long num3 = DateTime.UtcNow.Ticks / 10000;
				resetRing.ApplyStartSkew(num3 - num2.Value);
			}
		}
	}
}

public static class NodeInstantDamage
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.Frame != Entity.mainPlayer.Frame)
		{
			return;
		}
		List<int> list = props["DamageTypes"].Select((JToken x) => (int)x).ToList();
		List<string> list2 = props["Targets"].Select((JToken x) => (string?)x).ToList();
		List<int> list3 = props["Damages"].Select((JToken x) => (int)x).ToList();
		List<int> list4 = props["TargetHPs"].Select((JToken x) => (int)x).ToList();
		for (int num = 0; num < list2.Count; num++)
		{
			Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(list2[num]);
			if (entityByTargetString != null)
			{
				if (!entityByTargetString.isPlayer || entityByTargetString.isMainPlayer)
				{
					entityByTargetString.damageTickets++;
					Singleton<Combat>.Instance.PlayHitSound(caster, entityByTargetString, (BattleTextBouncer.DamageType)list[num]);
					GameObject overridePrefab = (caster.isPlayer ? Singleton<Combat>.Instance.popupStream : null);
					BattleTextBouncer.Dispense(caster, entityByTargetString, list3[num], list4[num], (BattleTextBouncer.DamageType)list[num], overridePrefab);
				}
				entityByTargetString.HP = list4[num];
			}
		}
		string text = props["ImpactSound"]?.Value<string>() ?? "";
		if (!string.IsNullOrEmpty(text))
		{
			AudioManager.Play2DPannedSound(Entity.mainPlayer.getGameObject().transform, Entity.mainPlayer.getGameObject().transform, Relevance.Me, text);
		}
	}
}

public static class NodeInterruptable
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer)
		{
			string key = props["Animation"].Value<string>();
			float value = props["Time"].Value<float>();
			caster.animation.queuedInterrupt[key] = value;
		}
	}
}

public static class NodeMaxSkillHold
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer)
		{
			int slot = props["Slot"].Value<int>();
			int duration = props["Time"].Value<int>();
			Transform transform = Singleton<UISkillSlots>.Instance.gameObject.transform.Find("IndicatorHoldSkill");
			if (transform != null)
			{
				GameObject gameObject = transform.gameObject;
				MaximumSkillHoldBar component = gameObject.GetComponent<MaximumSkillHoldBar>();
				component.SetDuration(duration);
				component.SetSlot(slot);
				gameObject.SetActive(value: true);
			}
		}
	}
}

public static class NodeMessage
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer)
		{
			string title = props["Title"].Value<string>();
			string? message = props["Text"].Value<string>();
			UIMessageBox.Title = title;
			UIMessageBox.Message = message;
			Singleton<UIManager>.Instance.ChangeState("Message");
		}
	}
}

public static class NodeMonTransform
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster == null)
		{
			return;
		}
		try
		{
			JToken? jToken = props["detransform"];
			if (jToken != null && jToken.Value<bool>())
			{
				caster.RemoveMonTransform();
			}
			else if (caster.inSameFrame())
			{
				AssetBundleData assetBundleData = props["Bundle"]?.ToObject<AssetBundleData>();
				string text = props["Linkage"]?.Value<string>();
				if (assetBundleData != null && !string.IsNullOrEmpty(text))
				{
					JToken jToken2 = props.SelectToken("Scale");
					float scale = ((jToken2 != null && jToken2.Type != JTokenType.Null) ? jToken2.Value<float>() : 1f);
					caster.ApplyMonTransform(assetBundleData, text, scale);
				}
			}
		}
		catch (Exception ex)
		{
			UnityEngine.Debug.LogError("NodeMonTransform: malformed props - " + ex.Message);
		}
	}
}

public static class NodeMonsterMove
{
	public static void TeleportApply(Entity caster, int instanceID, JObject props)
	{
		Apply(caster, props, snap: true);
	}

	public static void WalkApply(Entity caster, int instanceID, JObject props)
	{
		Apply(caster, props, snap: false);
	}

	private static void Apply(Entity caster, JObject props, bool snap)
	{
		if (!(caster is Monster) || Entity.mainPlayer == null || caster.Frame != Entity.mainPlayer.Frame || props.SelectToken("destX") == null || props.SelectToken("destY") == null)
		{
			return;
		}
		GameObject gameObject = caster.getGameObject();
		if (!(gameObject == null))
		{
			Vector3 footprintLocalPosition = caster.GetFootprintLocalPosition();
			Vector3 localPosition = gameObject.transform.localPosition;
			Vector3 vector = footprintLocalPosition - localPosition;
			Vector3 vector2 = new Vector3(props["destX"].Value<float>(), props["destY"].Value<float>(), localPosition.z) - vector;
			if (snap)
			{
				caster.setLocalPosition(vector2);
			}
			else if (caster.animation != null && caster.animation.walk != null)
			{
				Walk walk = caster.animation.walk;
				float valueOrDefault = (props.SelectToken("speed")?.Value<float?>()).GetValueOrDefault();
				walk.walkTo(vector2, (valueOrDefault > 0f) ? valueOrDefault : walk.speed, serverAuthored: true);
			}
		}
	}

	public static List<string> ReportPositions(string monsterString, JObject response, long responseTS)
	{
		Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(monsterString);
		if (entityByTargetString == null || Entity.mainPlayer == null || entityByTargetString.Frame != Entity.mainPlayer.Frame)
		{
			return new List<string>();
		}
		string text = response.SelectToken("Target")?.Value<string>();
		Entity entity = ((!string.IsNullOrEmpty(text)) ? Area.currentArea.GetEntityByTargetString(text) : entityByTargetString.target);
		if (entity == null || entity.getGameObject() == null)
		{
			return new List<string>();
		}
		CultureInfo invariantCulture = CultureInfo.InvariantCulture;
		Vector3 footprintLocalPosition = entityByTargetString.GetFootprintLocalPosition();
		Vector3 footprintLocalPosition2 = entity.GetFootprintLocalPosition();
		return new List<string>
		{
			monsterString,
			response["Name"].Value<string>(),
			footprintLocalPosition.x.ToString(invariantCulture),
			footprintLocalPosition.y.ToString(invariantCulture),
			footprintLocalPosition2.x.ToString(invariantCulture),
			footprintLocalPosition2.y.ToString(invariantCulture)
		};
	}
}

public static class NodeMoveTargets
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.inSameFrame())
		{
			return;
		}
		string text = props["Targets"].Value<string>();
		if (string.IsNullOrEmpty(text))
		{
			return;
		}
		List<Entity> list = new List<Entity>();
		List<string> list2 = text.Split(",").ToList();
		for (int i = 0; i < list2.Count; i++)
		{
			Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(list2[i]);
			if (entityByTargetString != null && entityByTargetString.currentState != Entity.State.Dead)
			{
				list.Add(entityByTargetString);
			}
		}
		CombatMovement.GroupMove(caster, list, props["OffsetX"].Value<float>(), props["Duration"].Value<int>());
	}
}

public static class NodeParticle
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		string text = props["Particle"]?.Value<string>();
		if (!string.IsNullOrEmpty(text))
		{
			Singleton<ParticlesManager>.Instance.QueueClassParticle(text, (caster as Player)?.classBundle);
		}
		string follow = ReadFollowMode(props);
		float animSpeed = props.SelectToken("AnimSpeed")?.Value<float>() ?? 1f;
		float? lifeSeconds = ((props.SelectToken("Lifetime") != null) ? new float?(props["Lifetime"].Value<float>() / 1000f) : ((float?)null));
		if (props.SelectToken("Animation") != null && props.SelectToken("Time") != null)
		{
			string text2 = props["Animation"].Value<string>();
			if (caster.animation != null && ((caster.animation.idleAnimation != null && text2 == caster.animation.idleAnimation.animationState) || (caster.animation.combatIdle != null && text2 == caster.animation.combatIdle.animationState)))
			{
				ParticleTime particleTime = new ParticleTime
				{
					targetStrings = props["Targets"].Select((JToken jToken) => (string?)jToken).ToList(),
					particleFX = text,
					animationKey = text2,
					time = props["Time"].Value<float>(),
					x = props["X"].Value<float>(),
					y = props["Y"].Value<float>(),
					follow = follow,
					animSpeed = animSpeed
				};
				caster.animation.AddIdleParticleCue(particleTime);
				Animator animator = caster.animation.animator;
				int num = Animator.StringToHash(text2);
				if (!(animator != null) || animator.GetCurrentAnimatorStateInfo(0).shortNameHash != num || animator.IsInTransition(0))
				{
					return;
				}
				List<GameObject> orAddPersistentBucket = caster.animation.GetOrAddPersistentBucket(num);
				for (int num2 = 0; num2 < particleTime.targetStrings.Count; num2++)
				{
					Entity entity = Area.currentArea?.GetEntityByTargetString(particleTime.targetStrings[num2]);
					if (entity != null && entity.Frame == Entity.mainPlayer.Frame)
					{
						GameObject gameObject = SpawnParticle(entity, text, particleTime.x, particleTime.y, particleTime.follow, particleTime.animSpeed, persistent: true);
						if (gameObject != null)
						{
							orAddPersistentBucket.Add(gameObject);
						}
					}
				}
			}
			else if (!(caster.animation == null))
			{
				ParticleTime particleTime2 = new ParticleTime
				{
					targetStrings = props["Targets"].Select((JToken jToken) => (string?)jToken).ToList(),
					particleFX = text,
					animationKey = text2,
					time = props["Time"].Value<float>(),
					x = props["X"].Value<float>(),
					y = props["Y"].Value<float>(),
					follow = follow,
					animSpeed = animSpeed,
					lifeSeconds = lifeSeconds
				};
				if (!caster.animation.queuedParticles.ContainsKey(particleTime2.animationKey))
				{
					caster.animation.queuedParticles[particleTime2.animationKey] = new List<ParticleTime>();
				}
				caster.animation.queuedParticles[particleTime2.animationKey].Add(particleTime2);
			}
			return;
		}
		List<string> list = props["Targets"].Select((JToken jToken) => (string?)jToken).ToList();
		float x = props["X"].Value<float>();
		float y = props["Y"].Value<float>();
		for (int num3 = 0; num3 < list.Count; num3++)
		{
			Entity entity2 = Area.currentArea?.GetEntityByTargetString(list[num3]);
			if (entity2 != null && entity2.Frame == Entity.mainPlayer.Frame)
			{
				SpawnParticle(entity2, text, x, y, follow, animSpeed, persistent: false, lifeSeconds);
			}
		}
	}

	private static string ReadFollowMode(JObject props)
	{
		JToken jToken = props.SelectToken("Follow");
		if (jToken == null)
		{
			return "No Follow";
		}
		if (jToken.Type == JTokenType.Boolean)
		{
			if (!jToken.Value<bool>())
			{
				return "No Follow";
			}
			return "Follow";
		}
		return jToken.Value<string>() ?? "No Follow";
	}

	private static bool IsSubEmitter(ParticleSystem ps, ParticleSystem[] all)
	{
		for (int i = 0; i < all.Length; i++)
		{
			ParticleSystem.SubEmittersModule subEmitters = all[i].subEmitters;
			for (int j = 0; j < subEmitters.subEmittersCount; j++)
			{
				if (subEmitters.GetSubEmitterSystem(j) == ps)
				{
					return true;
				}
			}
		}
		return false;
	}

	public static GameObject SpawnParticle(Entity target, string fx, float x, float y, string follow = "No Follow", float animSpeed = 1f, bool persistent = false, float? lifeSeconds = null)
	{
		if (target == null || target.getGameObject() == null)
		{
			return null;
		}
		GameObject particle = Singleton<ParticlesManager>.Instance.GetParticle(fx);
		if (particle == null)
		{
			return null;
		}
		if (Entity.mainPlayer == null || Entity.mainPlayer.getGameObject() == null)
		{
			return null;
		}
		bool flag = follow == "Follow Offhand";
		GameObject gameObject = UnityEngine.Object.Instantiate(particle, flag ? target.getGameObject().transform : Singleton<CombatPlayer>.Instance.ProjectileContainer.transform);
		if (gameObject.GetComponent<SpriteRenderer>() != null)
		{
			gameObject.GetComponent<SpriteRenderer>().sortingLayerName = "Default";
		}
		if (!Mathf.Approximately(animSpeed, 1f))
		{
			Animator[] componentsInChildren = gameObject.GetComponentsInChildren<Animator>(includeInactive: true);
			for (int i = 0; i < componentsInChildren.Length; i++)
			{
				componentsInChildren[i].speed = animSpeed;
			}
			ParticleSystem[] componentsInChildren2 = gameObject.GetComponentsInChildren<ParticleSystem>(includeInactive: true);
			for (int j = 0; j < componentsInChildren2.Length; j++)
			{
				if (!IsSubEmitter(componentsInChildren2[j], componentsInChildren2))
				{
					ParticleSystem.MainModule main = componentsInChildren2[j].main;
					main.simulationSpeed = animSpeed;
				}
			}
		}
		Transform transform = target.getGameObject().transform;
		if (flag)
		{
			gameObject.transform.localPosition = new Vector3(x, y, 0f);
			Singleton<Combat>.Instance.ScaleVFXUnderParent(gameObject);
		}
		else
		{
			Singleton<Combat>.Instance.ScaleVFX(gameObject, transform.localScale.x < 0f);
			gameObject.transform.position = transform.TransformPoint(new Vector3(x, y, 0f));
		}
		gameObject.SetActive(value: false);
		if (!persistent)
		{
			TimedKill component = gameObject.GetComponent<TimedKill>();
			if (component == null)
			{
				component = gameObject.AddComponent<TimedKill>();
				component.seconds = lifeSeconds ?? 3f;
			}
			else if (lifeSeconds.HasValue)
			{
				component.seconds = lifeSeconds.Value;
			}
		}
		switch (follow)
		{
		case "Follow Offhand":
		{
			Transform transform2 = target.getGameObject().transform.Find("avatar/actor/Bones/BackHandBone");
			if (transform2 != null)
			{
				SortingGroup sortingGroup = transform2.GetComponent<SortingGroup>() ?? transform2.GetComponentInChildren<SortingGroup>(includeInactive: true);
				int sortingOrder = ((sortingGroup != null) ? sortingGroup.sortingOrder : 4);
				gameObject.transform.SetParent(target.getGameObject().transform, worldPositionStays: true);
				(gameObject.GetComponent<SortingGroup>() ?? gameObject.AddComponent<SortingGroup>()).sortingOrder = sortingOrder;
				gameObject.SetActive(value: true);
				return gameObject;
			}
			break;
		}
		case "Follow":
		case "Follow Until Move":
		{
			FollowEntity followEntity = gameObject.AddComponent<FollowEntity>();
			followEntity.entity = target;
			followEntity.offsetLocal = new Vector3(x, y, 0f);
			followEntity.untilMove = follow == "Follow Until Move";
			break;
		}
		}
		if (Singleton<Combat>.Instance != null)
		{
			Singleton<Combat>.Instance.ApplyZOrder(gameObject, target, gameObject.transform.position.y - target.getGameObject().transform.position.y);
		}
		gameObject.SetActive(value: true);
		return gameObject;
	}

	public static void SpawnMonsterParticle(Entity e, string VFX, float x, float y, float dur = 1.5f)
	{
		try
		{
			GameObject gameObject = UnityEngine.Object.Instantiate(e.getGameObject().transform.Find(VFX).gameObject, Singleton<Game>.Instance.EntitiesContainer.transform);
			Vector3 localPosition = Entity.mainPlayer.getGameObject().transform.localPosition;
			gameObject.transform.localPosition = new Vector3(localPosition.x + x, localPosition.y + y, localPosition.z);
			gameObject.layer = LayerMask.NameToLayer("Level");
			gameObject.GetComponentInChildren<SpriteRenderer>().sortingLayerName = "Default";
			gameObject.AddComponent<TimedKill>().seconds = dur;
			gameObject.SetActive(value: true);
		}
		catch (NullReferenceException)
		{
		}
	}

	public static void SpawnMonsterParticleAt(Entity e, string VFX, Vector3 pos, float dur = 1.5f)
	{
		try
		{
			GameObject gameObject = UnityEngine.Object.Instantiate(e.getGameObject().transform.Find(VFX).gameObject, Singleton<Game>.Instance.EntitiesContainer.transform);
			gameObject.transform.localPosition = pos;
			gameObject.layer = LayerMask.NameToLayer("Level");
			gameObject.GetComponentInChildren<SpriteRenderer>().sortingLayerName = "Default";
			gameObject.AddComponent<TimedKill>().seconds = dur;
			gameObject.SetActive(value: true);
		}
		catch (NullReferenceException)
		{
		}
	}
}

public static class NodePlayerAnimation
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.inSameFrame() || caster.animation == null || caster.animation.animator == null)
		{
			return;
		}
		string text = props["Animation"].Value<string>();
		if (text.Contains(","))
		{
			string[] array = text.Split(",");
			List<string> list = array.Where((string a) => caster.animation.hasAnimation(a)).ToList();
			if (list.Count < 1)
			{
				return;
			}
			string text2 = list[new System.Random().Next(0, list.Count)];
			HashSet<string> hashSet = new HashSet<string>();
			for (int num = 0; num < array.Length; num++)
			{
				if (array[num] != text2)
				{
					hashSet.Add(array[num]);
				}
			}
			if (hashSet.Count > 0)
			{
				caster.animation.rejectedVariantsByInstance[instanceID] = hashSet;
			}
			text = text2;
		}
		float speed = ((props.SelectToken("Speed") != null) ? props["Speed"].Value<float>() : 1f);
		if (!caster.isMainPlayer)
		{
			if (!caster.isPlayer || caster.hasDamageQueue(instanceID))
			{
				CombatAnimationObject combatAnimationObject = new CombatAnimationObject(caster, instanceID, text, null, Skill.ActionType.Auto, speed);
				if (caster.hasDied || caster.currentState == Entity.State.Dead)
				{
					combatAnimationObject.Activate();
					combatAnimationObject.showDamage();
				}
				else
				{
					caster.animation.queuedAnimations.Enqueue(combatAnimationObject);
				}
			}
			else if (props.SelectToken("Priority") != null)
			{
				if (props["Priority"].Value<string>() == "Interrupt All")
				{
					caster.animation.Play(new InterruptAllPriorityAnimation(text, speed));
				}
				else
				{
					caster.animation.Play(new AttackAnimation(text, speed));
				}
			}
			else
			{
				caster.animation.Play(new AttackAnimation(text, speed));
			}
		}
		else if (caster.hasDamageQueue(instanceID))
		{
			int num2 = ((CombatPlayer.CurrentNodeSlot >= 0) ? CombatPlayer.CurrentNodeSlot : 0);
			Skill.ActionType act = Entity.myPlayerData.ClassData.getSkill(num2)?.Action ?? Skill.ActionType.Regular;
			CombatAnimationObject combatAnimationObject2 = new CombatAnimationObject(caster, instanceID, text, null, act, speed);
			if (caster.hasDied || caster.currentState == Entity.State.Dead)
			{
				combatAnimationObject2.Activate();
				combatAnimationObject2.showDamage();
			}
			else
			{
				caster.animation.queuedAnimations.Enqueue(combatAnimationObject2);
			}
		}
		else if (props.SelectToken("Priority") != null)
		{
			string text3 = props["Priority"].Value<string>();
			AnimationObject animationObject = null;
			switch (text3)
			{
			case "Low":
				animationObject = new LowPriorityAnimation(text, isCancellableByMovement: false, speed);
				break;
			case "Medium":
				animationObject = new MediumPriorityAnimation(text, speed);
				break;
			case "Attack":
				animationObject = new AttackAnimation(text, speed);
				break;
			case "High":
				animationObject = new HighPriorityAnimation(text, speed);
				break;
			case "Interrupt All":
				animationObject = new InterruptAllPriorityAnimation(text, speed);
				animationObject.instanceID = instanceID;
				caster.animation.queuedAnimations.Enqueue(animationObject);
				return;
			case "Looping":
				animationObject = new LoopingCombatAnimation(text, speed);
				animationObject.instanceID = instanceID;
				caster.animation.queuedAnimations.Enqueue(animationObject);
				return;
			}
			caster.animation.Play(animationObject);
		}
		else
		{
			caster.animation.Play(new MediumPriorityAnimation(text, speed));
		}
	}
}

public static class NodePlayerHitStream
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.Frame != Entity.mainPlayer.Frame)
		{
			return;
		}
		float value = props["X"]?.Value<float>() ?? 0f;
		float value2 = props["Y"]?.Value<float>() ?? 0f;
		float value3 = props["Width"]?.Value<float>() ?? 1f;
		float value4 = props["Height"]?.Value<float>() ?? 1f;
		int durationMs = props["Duration"]?.Value<int>() ?? 0;
		int intervalMs = props["Interval"]?.Value<int>() ?? 1000;
		int num = props["Slot"]?.Value<int>() ?? (-1);
		string vfx = props["VFX"]?.Value<string>();
		Entity entity = caster;
		string text = props["OriginTarget"]?.Value<string>();
		if (!string.IsNullOrEmpty(text))
		{
			Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(text);
			if (entityByTargetString == null)
			{
				return;
			}
			entity = entityByTargetString;
		}
		GameObject gameObject = entity.getGameObject();
		if (!(gameObject == null))
		{
			float num2 = entity.ScaleToEntity(value);
			float num3 = entity.ScaleToEntity(value2, isX: false);
			float num4 = entity.ScaleToEntity(value3);
			float num5 = entity.ScaleToEntity(value4, isX: false);
			if (gameObject.transform.localScale.x < 0f)
			{
				num2 *= -1f;
			}
			Vector3 center = new Vector3(gameObject.transform.position.x + num2, gameObject.transform.position.y + num3, 0f);
			Vector2 size = new Vector2((float)Math.Round(num4), (float)Math.Round(num5));
			PlayerHotTile playerHotTile = caster.getGameObject().AddComponent<PlayerHotTile>();
			playerHotTile.caster = caster;
			playerHotTile.center = center;
			playerHotTile.size = size;
			playerHotTile.durationMs = durationMs;
			playerHotTile.intervalMs = intervalMs;
			playerHotTile.pmahsKey = $"PlayerHitStream:{num}";
			playerHotTile.vfx = vfx;
			playerHotTile.Begin();
		}
	}
}

public static class NodeRange
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.inSameFrame())
		{
			return;
		}
		if (props.SelectToken("Target") != null)
		{
			string ts = props["Target"].Value<string>();
			Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(ts);
			if (entityByTargetString == null || entityByTargetString.getGameObject() == null)
			{
				caster.target = null;
				return;
			}
			caster.setTarget(entityByTargetString);
			if (caster.getGameObject() == null)
			{
				caster.target = null;
				return;
			}
			bool flag = entityByTargetString.getGameObject().transform.position.x < caster.getGameObject().transform.position.x;
			Skill.ActionType actionType = Skill.ActionType.Regular;
			if (caster.isMainPlayer && CombatPlayer.CurrentNodeSlot >= 0)
			{
				Skill skill = Entity.myPlayerData.ClassData.getSkill(CombatPlayer.CurrentNodeSlot);
				if (skill != null)
				{
					actionType = skill.Action;
				}
			}
			if (actionType < Skill.ActionType.Flex)
			{
				caster.direction = ((!flag) ? Entity.Direction.Right : Entity.Direction.Left);
			}
		}
		else
		{
			caster.target = null;
		}
	}

	private static List<string> FindEntities(Entity caster, bool isMonster, bool checkSight, float hRange, float vRange)
	{
		if (caster?.getGameObject() == null)
		{
			return new List<string>();
		}
		Vector3 casterPos = caster.getGameObject().transform.position;
		Vector2 vector = new Vector2(hRange, vRange);
		vector.x = Entity.mainPlayer.ScaleToEntity(vector.x);
		vector.y = Entity.mainPlayer.ScaleToEntity(vector.y, isX: false);
		bool flag = caster.getGameObject().transform.localScale.x < 0f;
		Vector3 vector2;
		Vector2 size;
		if (checkSight)
		{
			float num = (flag ? ((0f - vector.x) / 2f) : (vector.x / 2f));
			vector2 = new Vector3(casterPos.x + num, casterPos.y, casterPos.z);
			size = vector;
		}
		else
		{
			vector2 = casterPos;
			size = new Vector2(vector.x * 2f, vector.y);
		}
		if (Singleton<Combat>.Instance.showHitboxes)
		{
			GameObject gameObject = new GameObject("VisualShape");
			zgizmoTest obj = gameObject.AddComponent<zgizmoTest>();
			obj.center = vector2;
			obj.size = size;
			UnityEngine.Object.Destroy(gameObject, 3f);
		}
		Entity entity = (from hit in Physics2D.OverlapBoxAll(vector2, size, 0f, LayerMask.GetMask("Entities"))
			where hit != null
			select hit.GetComponentInChildren<EntityAnimationControl>() into hit
			where hit != null
			select hit.character into e
			where e != null && e != caster
			where !isMonster || (e.currentState != Entity.State.Dead && e.reactionType == Entity.ReactionType.Hostile)
			where Singleton<Combat>.Instance.isTargetInCamera(e)
			where !checkSight || isInSight(caster, e)
			orderby Vector3.Distance(e.getGameObject().transform.position, casterPos), isInSight(caster, e) descending
			select e).FirstOrDefault();
		if (entity != null)
		{
			caster.setTarget(entity);
			return new List<string> { "true", entity.TargetString };
		}
		return new List<string>();
	}

	public static List<string> Input(Entity caster, JObject props)
	{
		List<string> list = new List<string>();
		string text = props["mode"].Value<string>();
		float num = props["hrange"].Value<float>();
		float num2 = props["vrange"].Value<float>();
		int num3 = props["slot"].Value<int>();
		bool flag = props.SelectToken("charge") != null && props["charge"].Value<bool>();
		bool holdAtRange = props.SelectToken("holdAtRange") != null && props["holdAtRange"].Value<bool>();
		list.Add(text);
		string text2 = "";
		if (text == "find")
		{
			string text3 = props["type"].Value<string>();
			List<string> list2 = new List<string>();
			if (text3 != "Hostile")
			{
				list2 = FindEntities(caster, isMonster: false, checkSight: true, num, num2);
				if (list2.Count == 0)
				{
					list2 = FindEntities(caster, isMonster: false, checkSight: false, num, num2);
				}
			}
			else
			{
				list2 = FindEntities(caster, isMonster: true, checkSight: true, num, num2);
				if (list2.Count == 0)
				{
					list2 = FindEntities(caster, isMonster: true, checkSight: false, num, num2);
				}
			}
			if (list2.Count == 0)
			{
				bool isMon = text3 == "Hostile";
				Vector3 casterPos = caster.getGameObject().transform.position;
				Entity entity = (from e in Area.currentArea.allEntities
					where e != null && e != caster && e.getGameObject() != null
					where e.Frame == caster.Frame
					where !isMon || (e.currentState != Entity.State.Dead && e.reactionType == Entity.ReactionType.Hostile)
					where Singleton<Combat>.Instance.isTargetInCamera(e)
					orderby Vector3.Distance(e.getGameObject().transform.position, casterPos), isInSight(caster, e) descending
					select e).FirstOrDefault();
				if (entity != null)
				{
					caster.setTarget(entity);
					list2 = new List<string> { "true", entity.TargetString };
				}
			}
			if (list2.Count >= 2)
			{
				list.AddRange(list2);
				text2 = list2[1];
			}
			else
			{
				list.Add("false");
				Singleton<Combat>.Instance.DisplayCombatMessage((caster.target == null) ? "No available targets found!" : "Target out of range! Move closer to your target.");
			}
		}
		else if (text == "validate")
		{
			string text4 = props["target"].Value<string>();
			Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(text4);
			if (entityByTargetString == null || entityByTargetString.hasDied || entityByTargetString.currentState == Entity.State.Dead)
			{
				Singleton<Combat>.Instance.DisplayCombatMessage("No available targets found!");
				list.Add("false");
				SetSlotToCD(caster, num3);
				return list;
			}
			if (Singleton<Combat>.Instance.isTargetInRange(caster, entityByTargetString, num, num2))
			{
				list.Add("true");
				list.Add(entityByTargetString.TargetString);
			}
			text2 = text4;
		}
		if (text2 != "")
		{
			Entity entityByTargetString2 = Area.currentArea.GetEntityByTargetString(text2);
			if (entityByTargetString2.getGameObject() != null && caster.getGameObject() != null)
			{
				bool flag2 = entityByTargetString2.getGameObject().transform.position.x < caster.getGameObject().transform.position.x;
				Skill skill = Entity.myPlayerData.ClassData.getSkill(num3);
				if (skill == null || skill.Action < Skill.ActionType.Flex)
				{
					caster.direction = ((!flag2) ? Entity.Direction.Right : Entity.Direction.Left);
				}
			}
			if (num3 == 0 && Singleton<Combat>.Instance.isSkillAuto(num3))
			{
				Singleton<Combat>.Instance.autoHRange = num;
				Singleton<Combat>.Instance.autoVRange = num2;
			}
			bool flag3 = (Singleton<Combat>.Instance.isSkillAuto(num3) && text == "find") || (!Singleton<Combat>.Instance.isSkillAuto(num3) && flag);
			if (!Singleton<Combat>.Instance.isTargetInRange(caster, entityByTargetString2, num, num2) && flag3)
			{
				if (caster.Charge(num3, num, num2, holdAtRange) && Singleton<Combat>.Instance.ExecutionState.ChargingSlot.HasValue)
				{
					if (text == "validate" && list.Count < 2)
					{
						list.Add("true");
						list.Add(entityByTargetString2.TargetString);
					}
					Singleton<Combat>.Instance.ExecutionState.SetChargePacket(new List<string>(list), props.SelectToken("contextId")?.Value<string>());
					return new List<string>();
				}
				return new List<string> { text, "false" };
			}
		}
		if (text == "validate" && list.Count < 2)
		{
			list.Add("false");
			Singleton<Combat>.Instance.DisplayCombatMessage((caster.target == null) ? "No available targets found!" : "Target out of range! Move closer to your target.");
		}
		return list;
	}

	private static void SetSlotToCD(Entity caster, int slot)
	{
		if (Entity.myPlayerData.ClassData.getSkill(slot).Action == Skill.ActionType.Auto)
		{
			SkillSlotButton slot2 = Singleton<UISkillSlots>.Instance.GetSlot(0);
			if (slot2 != null)
			{
				slot2.showCooldown(100f);
			}
		}
	}

	public static bool isInSight(Entity src, Entity tgt)
	{
		if (src?.getGameObject() == null || tgt?.getGameObject() == null)
		{
			return false;
		}
		Vector2 vector = src.getGameObject().transform.position;
		Vector2 vector2 = tgt.getGameObject().transform.position;
		bool num = src.getGameObject().transform.localScale.x < 0f;
		bool flag = vector2.x < vector.x;
		if (num != flag)
		{
			return false;
		}
		return true;
	}

	public static List<string> MonsterInput(string MonsterString, JObject response, long responseTS)
	{
		List<string> list = new List<string>();
		list.Add(MonsterString);
		list.Add("Range");
		Monster monster = Area.currentArea.GetMonstersInFrame(Entity.mainPlayer.Frame).FirstOrDefault((Monster m) => m.TargetString == MonsterString);
		if (monster == null)
		{
			UnityEngine.Debug.LogError("[NodeRange] Monster not found: " + MonsterString);
			return list;
		}
		list.Add(monster.getPosition().x.ToString());
		list.Add(monster.getPosition().y.ToString());
		list.Add(Entity.mainPlayer.getPosition().x.ToString());
		list.Add(Entity.mainPlayer.getPosition().y.ToString());
		return list;
	}
}

public static class NodeRangeMulti
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.inSameFrame() || caster.target == null || caster.target.currentState != Entity.State.Dead || props.SelectToken("Targets") == null)
		{
			return;
		}
		string text = props["Targets"][0]?.Value<string>();
		if (!string.IsNullOrEmpty(text))
		{
			Entity entity = Area.currentArea?.GetEntityByTargetString(text);
			if (entity != null)
			{
				caster.setTarget(entity);
			}
		}
	}

	public static List<string> Input(Entity caster, JObject props)
	{
		List<string> list = new List<string>();
		int num = props["max"].Value<int>();
		float hRange = props["hrange"].Value<float>();
		float vRange = props["vrange"].Value<float>();
		if (props["target"].Value<string>() == "Hostile")
		{
			List<Monster> monstersInFrame = Area.currentArea.GetMonstersInFrame(caster.Frame);
			monstersInFrame.Sort(new TargetDistanceComparer(caster));
			foreach (Monster item in monstersInFrame)
			{
				if (item.currentState != Entity.State.Dead && item.reactionType == Entity.ReactionType.Hostile && Singleton<Combat>.Instance.isTargetInRange(caster, item, hRange, vRange) && isTargetInCamera(item))
				{
					if (list.Count < 1)
					{
						list.Add("true");
					}
					list.Add(item.TargetString);
					if (list.Count - 1 >= num)
					{
						break;
					}
				}
			}
		}
		else
		{
			List<Entity> list2 = Entity.GetPlayersInCell(caster.Frame).ToList();
			list2.Sort(new TargetDistanceComparer(caster));
			foreach (Entity item2 in list2)
			{
				if (item2 is Player { currentState: not Entity.State.Dead } player && Singleton<Combat>.Instance.isTargetInRange(caster, player, hRange, vRange))
				{
					if (list.Count < 1)
					{
						list.Add("true");
					}
					list.Add(player.TargetString);
					if (list.Count - 1 >= num)
					{
						break;
					}
				}
			}
		}
		if (list.Count < 1)
		{
			list.Add("false");
			Singleton<Combat>.Instance.DisplayCombatMessage("No targets found or out of range!");
		}
		return list;
	}

	private static bool isTargetInCamera(Entity tgt)
	{
		if (tgt == null || tgt.getGameObject() == null)
		{
			return false;
		}
		Camera main = Camera.main;
		if (main == null)
		{
			return true;
		}
		Vector3 vector = main.WorldToViewportPoint(tgt.getGameObject().transform.position);
		if (vector.x > 0f)
		{
			return (double)vector.x < 1.015;
		}
		return false;
	}
}

public static class NodeResource
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.inSameFrame())
		{
			return;
		}
		int num = props["Amount"].Value<int>();
		if (caster.hasDamageQueue(instanceID))
		{
			caster.updateDamageQueueResource(instanceID, num);
			return;
		}
		caster.RP = num;
		if (!caster.isMainPlayer)
		{
			return;
		}
		SkillSlotButton[] slots = Singleton<UISkillSlots>.Instance.GetSlots();
		foreach (SkillSlotButton skillSlotButton in slots)
		{
			if (!(skillSlotButton == null))
			{
				skillSlotButton.checkMana();
			}
		}
	}
}

public static class NodeRestrict
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer)
		{
			bool direction = props.SelectToken("Direction") != null && props["Direction"].Value<bool>();
			bool movement = props["Movement"].Value<bool>();
			bool skills = props["Skills"].Value<bool>();
			string text = props["Animation"].Value<string>();
			string text2 = props.SelectToken("ReleaseMode")?.Value<string>();
			RestrictionReleaseMode restrictionReleaseMode = ((text2 == "OnExit") ? RestrictionReleaseMode.OnExit : ((text2 == "Manual") ? RestrictionReleaseMode.Manual : RestrictionReleaseMode.AtTime));
			RestrictionReleaseMode restrictionReleaseMode2 = restrictionReleaseMode;
			caster.animation.queuedInitRestriction[text] = new RestrictTime
			{
				direction = direction,
				movement = movement,
				skills = skills,
				slotException = ((props.SelectToken("Slot") != null) ? props["Slot"].Value<string>() : null),
				releaseMode = restrictionReleaseMode2
			};
			float time = ((restrictionReleaseMode2 == RestrictionReleaseMode.OnExit || restrictionReleaseMode2 == RestrictionReleaseMode.Manual) ? float.MaxValue : props["Time"].Value<float>());
			caster.animation.queuedRestriction[text] = new RestrictionReleaseTime
			{
				animationKey = text,
				time = time,
				releaseMode = restrictionReleaseMode2
			};
		}
	}
}

public static class NodeRestrictRelease
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer)
		{
			string text = props.SelectToken("Animation")?.Value<string>();
			if (string.IsNullOrEmpty(text))
			{
				caster.isDirectionBlocked = false;
				caster.isMovementBlocked = false;
				caster.isSkillBlocked = false;
				caster.slotException = null;
				caster.animation.ClearRestrictionTracking();
			}
			else if (caster.animation.activeRestrictionAnimation == text)
			{
				caster.isDirectionBlocked = false;
				caster.isMovementBlocked = false;
				caster.isSkillBlocked = false;
				caster.slotException = null;
				caster.animation.ClearRestrictionTracking();
			}
		}
	}
}

public static class NodeSetSkillIndex
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.isMainPlayer)
		{
			return;
		}
		string text = ((props.SelectToken("Icon") != null) ? props["Icon"].Value<string>() : null);
		if (string.IsNullOrEmpty(text))
		{
			return;
		}
		int slotNum = props["Slot"].Value<int>();
		SkillSlotButton slot = Singleton<UISkillSlots>.Instance.GetSlot(slotNum);
		if (!(slot == null))
		{
			if (slot.resetRing != null && slot.resetRing.gameObject.activeSelf)
			{
				slot.resetRing.Disable();
			}
			slot.sk.Icon = text;
			slot.SpawnIcons();
		}
	}
}

public static class NodeSkillGlow
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer)
		{
			int slotNum = props["Slot"].Value<int>();
			bool active = props["Active"]?.Value<bool>() ?? true;
			SkillSlotButton slot = Singleton<UISkillSlots>.Instance.GetSlot(slotNum);
			if (!(slot == null) && !(slot.glow == null))
			{
				slot.glow.SetActive(active);
			}
		}
	}
}

public static class NodeSoundFX
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.isMainPlayer)
		{
			return;
		}
		string text = props["Sound"].Value<string>();
		if (text.Contains(","))
		{
			string[] array = text.Split(",");
			text = array[Game.seed.Next(0, array.Length)];
		}
		float minPitch = ((props.SelectToken("MinPitch") != null) ? props["MinPitch"].Value<float>() : (-1f));
		float maxPitch = ((props.SelectToken("MaxPitch") != null) ? props["MaxPitch"].Value<float>() : (-1f));
		string text2 = props["Animation"].Value<string>();
		SoundTime soundTime = new SoundTime
		{
			sound = text,
			animationKey = text2,
			time = props["Time"].Value<float>(),
			minPitch = minPitch,
			maxPitch = maxPitch
		};
		if (text2.Contains(","))
		{
			string[] array2 = text2.Split(",");
			for (int i = 0; i < array2.Length; i++)
			{
				caster.animation.queuedSound[array2[i]] = soundTime;
			}
		}
		else
		{
			caster.animation.queuedSound[soundTime.animationKey] = soundTime;
		}
	}
}

public static class NodeSpawnPickup
{
	private static Texture2D _placeholderTexture;

	private static int _walkableMask = -1;

	private static readonly Vector2[] _probeDirs = new Vector2[8]
	{
		new Vector2(0f, 1f),
		new Vector2(0.7071f, 0.7071f),
		new Vector2(1f, 0f),
		new Vector2(0.7071f, -0.7071f),
		new Vector2(0f, -1f),
		new Vector2(-0.7071f, -0.7071f),
		new Vector2(-1f, 0f),
		new Vector2(-0.7071f, 0.7071f)
	};

	private static readonly float[] _probeRadii = new float[4] { 0.5f, 1f, 1.5f, 2f };

	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.isMainPlayer)
		{
			return;
		}
		int num = props["PickupId"].Value<int>();
		float num2 = props["SpawnOffsetX"].Value<float>();
		float num3 = props["SpawnOffsetY"].Value<float>();
		string text = props["OriginTarget"]?.Value<string>();
		Vector3 position;
		if (!string.IsNullOrEmpty(text))
		{
			Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(text);
			if (entityByTargetString == null)
			{
				return;
			}
			position = entityByTargetString.getGameObject().transform.position;
		}
		else
		{
			position = Entity.mainPlayer.getGameObject().transform.position;
		}
		float num4 = position.x + num2;
		float num5 = position.y + num3;
		if (!TryFindWalkable(new Vector2(num4, num5), out var found))
		{
			UnityEngine.Debug.LogWarning($"NodeSpawnPickup: no walkable tile near ({num4}, {num5}); skipping pickup {num}");
			return;
		}
		num4 = found.x;
		num5 = found.y;
		string text2 = props["Prefab"]?.Value<string>() ?? "";
		float value = props["CollisionWidth"]?.Value<float>() ?? 1f;
		float value2 = props["CollisionHeight"]?.Value<float>() ?? 1f;
		int num6 = props["IAcceptNextQuest"]?.Value<int>() ?? 0;
		float num7 = Entity.mainPlayer.ScaleToEntity(value);
		float num8 = Entity.mainPlayer.ScaleToEntity(value2, isX: false);
		GameObject gameObject2;
		if (!string.IsNullOrEmpty(text2))
		{
			GameObject gameObject = Resources.Load<GameObject>(text2);
			gameObject2 = ((!(gameObject != null)) ? CreatePlaceholder(num7, num8) : UnityEngine.Object.Instantiate(gameObject));
		}
		else
		{
			gameObject2 = CreatePlaceholder(num7, num8);
		}
		gameObject2.name = $"Pickup_{num}";
		gameObject2.transform.position = new Vector3(num4, num5, 0f);
		gameObject2.layer = LayerMask.NameToLayer("Level");
		BoxCollider2D boxCollider2D = gameObject2.GetComponent<BoxCollider2D>();
		if (boxCollider2D == null)
		{
			boxCollider2D = gameObject2.AddComponent<BoxCollider2D>();
		}
		boxCollider2D.isTrigger = true;
		boxCollider2D.size = new Vector2(num7, num8);
		PickupCollider pickupCollider = gameObject2.AddComponent<PickupCollider>();
		pickupCollider.PickupId = num.ToString();
		pickupCollider.TimeoutSeconds = ((num6 > 0) ? ((float)num6 / 1000f) : 0f);
		pickupCollider.CollisionWidth = num7;
		pickupCollider.CollisionHeight = num8;
	}

	private static bool TryFindWalkable(Vector2 desired, out Vector2 found)
	{
		if (_walkableMask < 0)
		{
			_walkableMask = LayerMask.GetMask("Walkable");
		}
		if (Physics2D.Raycast(desired, Vector2.zero, 1f, _walkableMask).collider != null)
		{
			found = desired;
			return true;
		}
		for (int i = 0; i < _probeRadii.Length; i++)
		{
			float num = _probeRadii[i];
			for (int j = 0; j < _probeDirs.Length; j++)
			{
				Vector2 vector = desired + _probeDirs[j] * num;
				if (Physics2D.Raycast(vector, Vector2.zero, 1f, _walkableMask).collider != null)
				{
					found = vector;
					return true;
				}
			}
		}
		found = desired;
		return false;
	}

	private static GameObject CreatePlaceholder(float width, float height)
	{
		if (_placeholderTexture == null)
		{
			_placeholderTexture = new Texture2D(1, 1);
			_placeholderTexture.SetPixel(0, 0, Color.white);
			_placeholderTexture.Apply();
		}
		GameObject gameObject = new GameObject();
		SpriteRenderer spriteRenderer = gameObject.AddComponent<SpriteRenderer>();
		spriteRenderer.sprite = Sprite.Create(_placeholderTexture, new Rect(0f, 0f, 1f, 1f), new Vector2(0.5f, 0.5f), 1f);
		spriteRenderer.color = new Color(1f, 0.84f, 0f, 0.8f);
		spriteRenderer.sortingOrder = 5;
		gameObject.transform.localScale = new Vector3(width, height, 1f);
		return gameObject;
	}
}

public static class NodeSpellAnimation
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!caster.inSameFrame() || (caster.isMainPlayer && !caster.hasDamageQueue(instanceID)))
		{
			return;
		}
		string text = props["FX"].Value<string>().ToUpper();
		string text2 = props["Animation"].Value<string>();
		string text3 = props["SpellGraphic"].Value<string>();
		string text4 = ((props.SelectToken("SpellImpact") != null) ? props["SpellImpact"].Value<string>() : "");
		string initAttach = ((props.SelectToken("AttachInit") != null) ? props["AttachInit"].Value<string>() : "CastAttach");
		string spellAttach = ((props.SelectToken("Attach") != null) ? props["Attach"].Value<string>() : "Cast");
		string impactAttach = ((props.SelectToken("AttachImpact") != null) ? props["AttachImpact"].Value<string>() : "Origin");
		bool spellFollow = props.SelectToken("Follow") != null && props["Follow"].Value<bool>();
		int impactId = props.SelectToken("impactId")?.Value<int>() ?? (-1);
		AssetBundleData bundle = (caster as Player)?.classBundle;
		if (!string.IsNullOrEmpty(text3))
		{
			Singleton<ParticlesManager>.Instance.QueueClassParticle(text3, bundle);
		}
		if (!string.IsNullOrEmpty(text4))
		{
			Singleton<ParticlesManager>.Instance.QueueClassParticle(text4, bundle);
		}
		AnimationData anim = new AnimationData
		{
			AnimationState = text2,
			SpellGraphic = text3,
			SpellImpact = text4,
			InitAttach = initAttach,
			SpellAttach = spellAttach,
			ImpactAttach = impactAttach,
			SpellFollow = spellFollow,
			ImpactId = impactId,
			XOffset = (props.SelectToken("X")?.Value<float>() ?? 0f),
			YOffset = (props.SelectToken("Y")?.Value<float>() ?? 0f),
			ProjectileEase = ReadEase(props),
			ProjectileSpeed = (props.SelectToken("ProjSpeed")?.Value<float>() ?? 0f)
		};
		SpellData spellData = null;
		switch (text)
		{
		case "ORIGIN":
		case "WRAPPER":
			spellData = new WrapperData(anim, caster);
			break;
		case "PROJECTILE":
			spellData = new ProjectileData(anim, caster);
			break;
		case "HOMING":
			spellData = new HomingData(anim, caster);
			break;
		case "METEOR":
			spellData = new MeteorData(anim, caster);
			break;
		}
		if (spellData == null)
		{
			return;
		}
		if (caster.isMainPlayer)
		{
			int currentNodeSlot = CombatPlayer.CurrentNodeSlot;
			Skill.ActionType act = ((currentNodeSlot >= 0) ? Entity.myPlayerData.ClassData.getSkill(currentNodeSlot) : null)?.Action ?? Skill.ActionType.Regular;
			CombatAnimationObject item = new CombatAnimationObject(caster, instanceID, text2, spellData, act, 1f);
			caster.animation.queuedAnimations.Enqueue(item);
			return;
		}
		string text5 = props.SelectToken("target")?.Value<string>();
		Entity entity = ((!string.IsNullOrEmpty(text5)) ? Area.currentArea.GetEntityByTargetString(text5) : null);
		if (entity != null)
		{
			SpellAnimationObject animationObject = new SpellAnimationObject(caster, entity, text2, spellData);
			caster.animation.Play(animationObject);
		}
	}

	private static string ReadEase(JObject props)
	{
		string text = props.SelectToken("Ease")?.Value<string>()?.ToLower();
		if (string.IsNullOrEmpty(text) || !Enum.TryParse<iTween.EaseType>(text, ignoreCase: true, out var _))
		{
			return null;
		}
		return text;
	}
}

public static class NodeStopChannel
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer)
		{
			Singleton<Combat>.Instance.stopSkillStream();
		}
	}
}

public static class NodeSwapSkill
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer)
		{
			int slot = props["Slot"].Value<int>();
			ResponseActions.SkillData sk = null;
			if (props["Skill"].Type != JTokenType.String)
			{
				sk = props["Skill"].ToObject<ResponseActions.SkillData>();
			}
			Entity.myPlayerData.ClassData.setSkill(slot, sk);
		}
	}
}

public static class NodeTileCluster
{
	public static List<string> MonsterInput(string MonsterString, JObject response, long responseTS)
	{
		Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(MonsterString);
		if (entityByTargetString == null || entityByTargetString.Frame != Entity.mainPlayer.Frame)
		{
			return new List<string>();
		}
		string animation = response["CastAnimation"]?.Value<string>() ?? "Castcharge";
		if (entityByTargetString.currentState != Entity.State.Dead)
		{
			entityByTargetString.animation.PlayAnimation(animation);
		}
		ClusterTile clusterTile = entityByTargetString.getGameObject().AddComponent<ClusterTile>();
		clusterTile.MonsterString = MonsterString;
		if (response.SelectToken("VFX") != null)
		{
			clusterTile.VFX = response["VFX"].Value<string>();
		}
		clusterTile.DuringAnimation = response["DuringAnimation"]?.Value<string>();
		clusterTile.FinishAnimation = response["FinishAnimation"]?.Value<string>();
		clusterTile.ImpactSound = response["ImpactSound"]?.Value<string>();
		clusterTile.OnFinish += OnFinish;
		if (response["ClusterOffsets"] is JArray { Count: >=16 } jArray)
		{
			List<Vector2> list = new List<Vector2>();
			for (int i = 0; i + 1 < jArray.Count; i += 2)
			{
				list.Add(new Vector2(jArray[i].Value<float>(), jArray[i + 1].Value<float>()));
			}
			clusterTile.ServerOffsets = list;
		}
		clusterTile.StartFill(response["Speed"].Value<float>(), new Vector2(response["ScaleX"].Value<float>(), response["ScaleY"].Value<float>()));
		return new List<string>();
	}

	public static void OnFinish(string MonsterString, int touchCount)
	{
		GeometryTileReport.ReportHits(MonsterString, "TileCluster", touchCount);
	}
}

public static class NodeTileMove
{
	public static List<string> MonsterInput(string MonsterString, JObject response, long responseTS)
	{
		Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(MonsterString);
		if (entityByTargetString == null || entityByTargetString.Frame != Entity.mainPlayer.Frame)
		{
			return new List<string>();
		}
		MoveTile moveTile = Entity.mainPlayer.getGameObject().AddComponent<MoveTile>();
		moveTile.MonsterString = entityByTargetString.TargetString;
		moveTile.CastAnimation = response["CastAnimation"]?.Value<string>();
		moveTile.FinishAnimation = response["FinishAnimation"]?.Value<string>();
		moveTile.Setup(response["Speed"].Value<float>());
		return new List<string>();
	}
}

public static class NodeTileSafe
{
	public static List<string> MonsterInput(string MonsterString, JObject response, long responseTS)
	{
		Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(MonsterString);
		if (entityByTargetString == null || entityByTargetString.Frame != Entity.mainPlayer.Frame)
		{
			return new List<string>();
		}
		string animation = response["CastAnimation"]?.Value<string>() ?? "Castcharge";
		if (entityByTargetString.currentState != Entity.State.Dead)
		{
			entityByTargetString.animation.PlayAnimation(animation);
		}
		SafeTile safeTile = Entity.mainPlayer.getGameObject().AddComponent<SafeTile>();
		if (response.SelectToken("VFX") != null)
		{
			safeTile.VFX = response["VFX"].Value<string>();
		}
		safeTile.MonsterString = MonsterString;
		safeTile.DuringAnimation = response["DuringAnimation"]?.Value<string>();
		safeTile.FinishAnimation = response["FinishAnimation"]?.Value<string>();
		safeTile.ImpactSound = response["ImpactSound"]?.Value<string>();
		safeTile.DelayedAnimation = response["DelayedAnimation"]?.Value<string>();
		safeTile.DelayedAnimationTime = response["DelayedAnimationTime"]?.Value<float>() ?? 0f;
		safeTile.OnFinish += OnFinish;
		if (response["SafeOffsetX"] != null && response["SafeOffsetY"] != null)
		{
			safeTile.HasServerOffset = true;
			safeTile.ServerOffsetX = response["SafeOffsetX"].Value<float>();
			safeTile.ServerOffsetY = response["SafeOffsetY"].Value<float>();
		}
		safeTile.Setup(Entity.mainPlayer, response["Speed"].Value<float>(), new Vector2(response["ScaleX"].Value<float>(), response["ScaleY"].Value<float>()));
		return new List<string>();
	}

	public static void OnFinish(string MonsterString, bool success)
	{
		if (!success)
		{
			Singleton<AEC>.Instance.sendRequest(new RequestMonHit(MonsterString, "TileSafe"));
		}
	}
}

public static class NodeTileTrack
{
	public static List<string> MonsterInput(string MonsterString, JObject response, long responseTS)
	{
		Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(MonsterString);
		if (entityByTargetString == null || entityByTargetString.Frame != Entity.mainPlayer.Frame)
		{
			return new List<string>();
		}
		string anim = response["CastAnimation"]?.Value<string>() ?? "Castcharge";
		entityByTargetString.animation.Play(new InterruptAllPriorityAnimation(anim, 1f));
		string? text = response["Track"].Value<string>();
		TrackTile.TileTrackType tileTrackType = TrackTile.TileTrackType.Sides;
		if (text == "Center")
		{
			tileTrackType = TrackTile.TileTrackType.Center;
		}
		string tileFillType = response["Shape"].Value<string>();
		TrackTile trackTile = Entity.mainPlayer.getGameObject().AddComponent<TrackTile>();
		if (response.SelectToken("VFX") != null)
		{
			trackTile.VFX = response["VFX"].Value<string>();
		}
		trackTile.MonsterString = MonsterString;
		trackTile.FinishAnimation = response["FinishAnimation"]?.Value<string>();
		trackTile.DelayedAnimation = response["DelayedAnimation"]?.Value<string>();
		trackTile.DelayedAnimationTime = response["DelayedAnimationTime"]?.Value<float>() ?? 0f;
		trackTile.OnFinish += OnFinish;
		trackTile.Setup(entityByTargetString, Entity.mainPlayer, tileTrackType, tileFillType, response["Speed"].Value<float>(), new Vector2(response["ScaleX"].Value<float>(), response["ScaleY"].Value<float>()));
		return new List<string>();
	}

	public static void OnFinish(string MonsterString, bool success)
	{
		if (success)
		{
			Singleton<AEC>.Instance.sendRequest(new RequestMonHit(MonsterString, "TileTrack"));
		}
	}
}

public static class NodeTileWave
{
	public static List<string> MonsterInput(string MonsterString, JObject response, long responseTS)
	{
		Entity entityByTargetString = Area.currentArea.GetEntityByTargetString(MonsterString);
		if (entityByTargetString == null || entityByTargetString.Frame != Entity.mainPlayer.Frame)
		{
			return new List<string>();
		}
		if (Resources.Load<GameObject>("Prefab/Tile Prefabs/WaveTile") != null)
		{
			string anim = response["CastAnimation"]?.Value<string>() ?? "Castcharge";
			if (entityByTargetString.currentState != Entity.State.Dead)
			{
				entityByTargetString.animation.Play(new InterruptAllPriorityAnimation(anim, 1f));
			}
			WaveTile waveTile = entityByTargetString.getGameObject().AddComponent<WaveTile>();
			waveTile.MonsterString = MonsterString;
			waveTile.DuringAnimation = response["DuringAnimation"]?.Value<string>();
			waveTile.FinishAnimation = response["FinishAnimation"]?.Value<string>();
			waveTile.ImpactSound = response["ImpactSound"]?.Value<string>();
			waveTile.OnHit += OnHit;
			waveTile.OnFinish += OnFinish;
			waveTile.StartFill(response["Speed"].Value<float>());
		}
		else
		{
			UnityEngine.Debug.LogError("WaveTile prefab is missing");
		}
		return new List<string>();
	}

	public static void OnHit(string MonsterString)
	{
		Singleton<AEC>.Instance.sendRequest(new RequestMonHit(MonsterString, "TileWave"));
	}

	public static void OnFinish(string MonsterString, bool success)
	{
		if (success)
		{
			Singleton<AEC>.Instance.sendRequest(new RequestMonHit(MonsterString, "TileWave"));
		}
	}
}

public static class NodeUpdateAnimation
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (!(caster.animation == null))
		{
			string text = props["Tag"].Value<string>();
			string anim = props["Value"].Value<string>();
			switch (text)
			{
			case "combatIdle":
				caster.animation.combatIdle = new LowPriorityAnimation(anim);
				break;
			case "walkAnimation":
				caster.animation.walkAnimation = new LowPriorityAnimation(anim);
				break;
			case "idleAnimation":
				caster.animation.idleAnimation = new LowPriorityAnimation(anim);
				break;
			}
		}
	}
}

public static class NodeUpdateIcon
{
	public static void Execute(Entity caster, int instanceID, JObject props)
	{
		if (caster.isMainPlayer)
		{
			int slotNum = props["Slot"].Value<int>();
			string icon = props["Icons"].Value<string>();
			SkillSlotButton slot = Singleton<UISkillSlots>.Instance.GetSlot(slotNum);
			if (!(slot == null))
			{
				slot.sk.Icon = icon;
				slot.SpawnIcons();
			}
		}
	}
}